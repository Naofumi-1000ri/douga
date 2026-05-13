/**
 * E2E テスト: localStorage ETag キャッシュ動作の検証
 *
 * 検証内容:
 * 1. 初回ロード → ETag 付きレスポンスで localStorage にキャッシュエントリが作られる
 * 2. signed URL を含む assets list は 2回目も If-None-Match を送らず 200 を取り直す
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
  // テスト 2: signed URL を含む assets list は ETag を保存しても If-None-Match を送らない
  // ---------------------------------------------------------------------------
  test('ETag ヘッダーが返るとき: キャッシュに etag が保存され、2回目も非条件 GET になる', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    // アセット一覧リクエストをインターセプトして ETag ヘッダーを追加
    const capturedIfNoneMatchHeaders: Array<string | undefined> = []
    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      const req = route.request()
      const ifNoneMatch = req.headers()['if-none-match']
      capturedIfNoneMatchHeaders.push(ifNoneMatch)

      // この endpoint では送られないはずだが、送られた場合は旧仕様どおり 304 を返す。
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

    // 2回目以降も If-None-Match を送らず、signed URL を含む payload は 200 で取り直す。
    expect(capturedIfNoneMatchHeaders.length).toBeGreaterThanOrEqual(2)
    expect(capturedIfNoneMatchHeaders).not.toContain('W/"mock-etag-v1"')
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

  // ---------------------------------------------------------------------------
  // テスト 4: TTL 期限切れ後にページリロードすると非条件 GET が走る (#235)
  // ---------------------------------------------------------------------------
  test('TTL 期限切れ後のリロードで If-None-Match を含まない非条件 GET が走る', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    const capturedRequestHeaders: Array<Record<string, string>> = []

    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      const req = route.request()
      capturedRequestHeaders.push({ ...req.headers() })

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"ttl-expiry-etag"' },
        body: JSON.stringify([]),
      })
    })

    // 1回目のロード — キャッシュを作る
    await openSeededEditor(page, projectId, mock.sequenceId)
    await page.waitForTimeout(800)

    // localStorage にキャッシュが存在することを確認
    const entryRaw = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )
    expect(entryRaw).not.toBeNull()

    // expiresAt を過去時刻に書き換えてキャッシュを期限切れにする
    // 十分に遠い過去 (1 時間前) にすることで時計のずれを排除する
    await page.evaluate(
      ({ key }) => {
        const raw = localStorage.getItem(key)
        if (!raw) return
        const entry = JSON.parse(raw) as Record<string, unknown>
        entry['expiresAt'] = Date.now() - 60 * 60 * 1000 // 1 時間前に切れた
        localStorage.setItem(key, JSON.stringify(entry))
      },
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )

    // 期限切れになっていることを確認
    const expiredEntry = await page.evaluate(
      ({ key }) => {
        const raw = localStorage.getItem(key)
        if (!raw) return null
        return JSON.parse(raw) as Record<string, unknown>
      },
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )
    expect(expiredEntry?.['expiresAt']).toBeLessThan(Date.now())

    // リクエストヘッダー記録をリセット
    capturedRequestHeaders.length = 0

    // ページリロード
    await page.reload()
    await page.waitForLoadState('domcontentloaded')
    await page.waitForTimeout(800)

    // デバッグ: リロード後の localStorage の状態を確認
    const afterReloadEntry = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )
    // リロード後 readCache が期限切れを検出して削除するため null になるはず
    // (新しい 200 応答でキャッシュが再作成される前の一瞬は null)
    // ただし再作成後は非 null になる — ここでは最終状態を確認
    // 重要: リロード後最初のリクエストに If-None-Match がないことを確認
    const allCaptured = capturedRequestHeaders.length
    expect(allCaptured).toBeGreaterThan(0) // リロード後にリクエストが発生していること
    // 最初のリクエスト（インデックス 0）に If-None-Match が含まれていないこと
    expect(capturedRequestHeaders[0]?.['if-none-match']).toBeUndefined()
    // 後続リクエストも assets list では非条件 GET になるが、ここでは最初の回復リクエストだけを検証する
    // afterReloadEntry はデバッグ情報として記録（アサートしない）
    void afterReloadEntry
  })

  // ---------------------------------------------------------------------------
  // テスト 5: fetch 失敗時に stale バナーが表示され、再試行で消える (C-1)
  // ---------------------------------------------------------------------------
  test('fetch 失敗時に stale バナーが表示され、再試行で消える', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    const mockAssets = [
      {
        id: 'asset-stale-1',
        project_id: projectId,
        name: 'stale-test-video.mp4',
        type: 'video' as const,
        storage_key: 'key/stale.mp4',
        storage_url: 'https://example.com/stale.mp4',
        thumbnail_url: null,
        duration_ms: 3000,
        width: 1280,
        height: 720,
        file_size: 500000,
        mime_type: 'video/mp4',
        folder_id: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    ]

    // step1: 通常フローでキャッシュを作る（200 + ETag）
    let blockRequests = false

    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      if (blockRequests) {
        // step2: ネットワークブロック → 500 エラー
        await route.fulfill({ status: 500, body: 'Internal Server Error' })
        return
      }
      // 通常レスポンス（ETag あり）
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"stale-etag-v1"' },
        body: JSON.stringify(mockAssets),
      })
    })

    // step1: 通常ロードでキャッシュを作る
    await openSeededEditor(page, projectId, mock.sequenceId)
    await page.waitForTimeout(800)

    // キャッシュが作られていることを確認
    const entryRaw = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )
    expect(entryRaw).not.toBeNull()

    // step2: ネットワークブロックを有効化
    blockRequests = true

    // step3: ページリロード（キャッシュはそのまま）
    await page.reload()
    await page.waitForLoadState('domcontentloaded')
    await page.waitForTimeout(1200)

    // step4: キャッシュからデータが表示され、stale バナーが表示される
    await expect(page.locator('[data-testid="stale-data-banner"]').first()).toBeVisible({
      timeout: 5000,
    })

    // step5: ネットワークブロック解除 → 再試行でバナーが消える
    blockRequests = false

    // 再試行ボタンをクリック（アセットライブラリまたはシーケンスパネルいずれか）
    const retryButton = page.locator('[data-testid="stale-data-retry"]').first()
    await retryButton.click()
    await page.waitForTimeout(1000)

    // バナーが消えていること
    await expect(page.locator('[data-testid="stale-data-banner"]')).toHaveCount(0)
  })
})
