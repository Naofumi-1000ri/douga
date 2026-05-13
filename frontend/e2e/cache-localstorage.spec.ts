/**
 * E2E: assets list must not render or persist cached signed-url payloads.
 */

import { test, expect, type Page } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'
import { SCHEMA_VERSION } from '../src/lib/cache/etagCache'

const ASSET_CACHE_KEY_PREFIX = 'cache:v1:assets:'

function assetCacheKey(projectId: string): string {
  return `${ASSET_CACHE_KEY_PREFIX}${projectId}`
}

async function readAssetCache(page: Page, projectId: string): Promise<string | null> {
  return page.evaluate((key) => localStorage.getItem(key), assetCacheKey(projectId))
}

function legacyAssetCache(projectId: string, payload: unknown): string {
  return JSON.stringify({
    schemaVersion: SCHEMA_VERSION,
    etag: 'W/"legacy-assets-etag"',
    payload,
    fetchedAt: Date.now() - 60_000,
    expiresAt: Date.now() + 86_400_000,
  })
}

test.describe('assets list signed URL cache boundary', () => {
  test('assets list uses a cache-busted no-store GET and does not write localStorage cache', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedRequests: Array<{ url: string; headers: Record<string, string> }> = []

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      const req = route.request()
      capturedRequests.push({ url: req.url(), headers: req.headers() })

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"assets-etag-ignored"' },
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    await expect.poll(() => capturedRequests.length).toBeGreaterThan(0)
    expect(capturedRequests[0].url).toContain('_signed_url_refresh=')
    expect(capturedRequests[0].headers['cache-control']).toBe('no-store')
    expect(capturedRequests[0].headers['pragma']).toBe('no-cache')
    expect(capturedRequests[0].headers['if-none-match']).toBeUndefined()
    await expect.poll(() => readAssetCache(page, projectId)).toBeNull()
  })

  test('asset refreshes never send If-None-Match even when the API returns ETag', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedHeaders: Array<Record<string, string>> = []
    const capturedUrls: string[] = []

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      const req = route.request()
      capturedHeaders.push(req.headers())
      capturedUrls.push(req.url())

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"assets-etag-ignored"' },
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)
    await expect.poll(() => capturedHeaders.length).toBeGreaterThan(0)

    await page.evaluate(() => {
      window.dispatchEvent(new Event('douga-assets-changed'))
    })

    await expect.poll(() => capturedHeaders.length).toBeGreaterThanOrEqual(2)
    expect(capturedHeaders.every((headers) => headers['if-none-match'] === undefined)).toBe(true)
    expect(capturedUrls.every((url) => url.includes('_signed_url_refresh='))).toBe(true)
    await expect.poll(() => readAssetCache(page, projectId)).toBeNull()
  })

  test('legacy assets localStorage entry is cleared instead of rendered', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const staleAssetName = 'expired-signed-url-video.mp4'

    await page.addInitScript(
      ({ key, value }) => localStorage.setItem(key, value),
      {
        key: assetCacheKey(projectId),
        value: legacyAssetCache(projectId, [
          {
            id: 'asset-stale-1',
            project_id: projectId,
            name: staleAssetName,
            type: 'video',
            storage_key: 'key/stale.mp4',
            storage_url: 'https://storage.googleapis.com/mock/stale.mp4?X-Goog-Date=20240101T000000Z&X-Goog-Expires=3600',
            thumbnail_url: null,
            duration_ms: 3000,
            width: 1280,
            height: 720,
            file_size: 500000,
            mime_type: 'video/mp4',
            folder_id: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
      }
    )

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    await expect.poll(() => readAssetCache(page, projectId)).toBeNull()
    await expect(page.getByText(staleAssetName)).toHaveCount(0)
  })

  test('asset fetch failure does not fall back to stale assets cache', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const staleAssetName = 'stale-cache-should-not-render.mp4'

    await page.addInitScript(
      ({ key, value }) => localStorage.setItem(key, value),
      {
        key: assetCacheKey(projectId),
        value: legacyAssetCache(projectId, [
          {
            id: 'asset-stale-2',
            project_id: projectId,
            name: staleAssetName,
            type: 'video',
            storage_key: 'key/stale-2.mp4',
            storage_url: 'https://storage.googleapis.com/mock/stale-2.mp4?X-Goog-Date=20240101T000000Z&X-Goog-Expires=3600',
            thumbnail_url: null,
            duration_ms: 3000,
            width: 1280,
            height: 720,
            file_size: 500000,
            mime_type: 'video/mp4',
            folder_id: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
      }
    )

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    await expect.poll(() => readAssetCache(page, projectId)).toBeNull()
    await expect(page.getByText(staleAssetName)).toHaveCount(0)
    await expect(page.locator('[data-testid="stale-data-banner"]')).toHaveCount(0)
  })
})
