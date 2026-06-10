/**
 * E2E: assets list must use fetchWithETag + validatePayload for ETag caching (#273).
 *
 * Changed from old behaviour (#242-#256 workaround):
 * - Before: clearCache + _signed_url_refresh param + Cache-Control: no-store (bypass)
 * - After:  fetchWithETag(conditionalRequests=false) + validatePayload(areSignedUrlsValid)
 *
 * conditionalRequests=false means:
 *   - If-None-Match is NOT sent (backend excludes signed URL fields from ETag hash)
 *   - Cache entries ARE written to localStorage for optimistic display
 *   - validatePayload drops the cache when signed URLs are near expiry
 *
 * Also covers sequences (24h TTL) ETag cache behaviour (#238 item2):
 *   - sequences list uses conditionalRequests=false (thumbnail_url is volatile)
 *   - sequences list ETag is written to localStorage after 200 response
 */

import { test, expect, type Page } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'
import { SCHEMA_VERSION } from '../src/lib/cache/etagCache'

// Cache key helpers — defined inline to avoid importing api modules that
// transitively pull in firebase.ts (which uses import.meta.env) and would
// crash the Playwright test-runner process before webServer starts.
function assetCacheKey(projectId: string): string {
  return `cache:v1:assets:${projectId}`
}

function sequenceListCacheKey(projectId: string): string {
  return `cache:v1:sequences:${projectId}`
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

// Stale asset with expired signed URL for validatePayload tests
function staleSignedUrl(): string {
  return 'https://storage.googleapis.com/mock/stale.mp4?X-Goog-Date=20200101T000000Z&X-Goog-Expires=3600'
}

test.describe('assets list ETag cache (#273)', () => {
  test('assets list uses fetchWithETag and writes localStorage cache when ETag is returned', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedRequests: Array<{ url: string; headers: Record<string, string> }> = []

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      const req = route.request()
      capturedRequests.push({ url: req.url(), headers: req.headers() })

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"assets-etag-v1"' },
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    await expect.poll(() => capturedRequests.length).toBeGreaterThan(0)

    // New behaviour: no _signed_url_refresh, no Cache-Control: no-store
    expect(capturedRequests[0].url).not.toContain('_signed_url_refresh=')
    expect(capturedRequests[0].headers['cache-control']).toBeUndefined()
    expect(capturedRequests[0].headers['pragma']).toBeUndefined()

    // conditionalRequests=false → If-None-Match must NOT be sent
    expect(capturedRequests[0].headers['if-none-match']).toBeUndefined()

    // ETag returned by server should be stored in localStorage
    await expect.poll(() => readAssetCache(page, projectId)).not.toBeNull()
    const cachedRaw = await readAssetCache(page, projectId)
    if (cachedRaw) {
      const cached = JSON.parse(cachedRaw) as { etag?: string }
      expect(cached.etag).toBe('W/"assets-etag-v1"')
    }
  })

  test('asset refreshes never send If-None-Match (conditionalRequests=false)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedHeaders: Array<Record<string, string>> = []

    await page.route(new RegExp(`/api/projects/${projectId}/assets(?:\\?.*)?$`), async (route) => {
      const req = route.request()
      capturedHeaders.push(req.headers())

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"assets-etag-v1"' },
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)
    await expect.poll(() => capturedHeaders.length).toBeGreaterThan(0)

    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('douga-assets-changed'))
    })

    await expect.poll(() => capturedHeaders.length).toBeGreaterThanOrEqual(2)

    // Even after cache is written, subsequent fetches must NOT send If-None-Match
    // because backend signs URLs on every request (exclude_keys design)
    expect(capturedHeaders.every((headers) => headers['if-none-match'] === undefined)).toBe(true)
  })

  test('validatePayload: cache with expired signed URL is dropped before fetch', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const freshAssetName = 'fresh-from-server.mp4'

    // Seed localStorage with a cache entry that has an expired signed URL
    await page.addInitScript(
      ({ key, value }) => localStorage.setItem(key, value),
      {
        key: assetCacheKey(projectId),
        value: legacyAssetCache(projectId, [
          {
            id: 'asset-stale-1',
            project_id: projectId,
            name: 'expired-signed-url-video.mp4',
            type: 'video',
            storage_key: 'key/stale.mp4',
            storage_url: staleSignedUrl(),
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
        headers: { etag: 'W/"fresh-etag"' },
        body: JSON.stringify([
          {
            id: 'asset-fresh-1',
            project_id: projectId,
            name: freshAssetName,
            type: 'video',
            storage_key: 'key/fresh.mp4',
            storage_url: '/plain/fresh.mp4',
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
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    // Expired cache should NOT be used for optimistic display; server result is shown
    await expect(page.getByText('expired-signed-url-video.mp4')).toHaveCount(0)
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
            storage_url: staleSignedUrl(),
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

    await expect(page.getByText(staleAssetName)).toHaveCount(0)
    await expect(page.locator('[data-testid="stale-data-banner"]')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// sequences list ETag cache (#238 item2)
// ---------------------------------------------------------------------------

async function readSequenceListCache(page: Page, projectId: string): Promise<string | null> {
  return page.evaluate((key) => localStorage.getItem(key), sequenceListCacheKey(projectId))
}

test.describe('sequences list ETag cache (#238)', () => {
  test('sequences list writes localStorage cache when ETag is returned', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedRequests: Array<{ headers: Record<string, string> }> = []

    await page.route(new RegExp(`/api/projects/${projectId}/sequences$`), async (route) => {
      const req = route.request()
      capturedRequests.push({ headers: req.headers() })

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"sequences-etag-v1"' },
        body: JSON.stringify([
          {
            id: mock.sequenceId,
            name: 'Main Sequence',
            version: 1,
            duration_ms: 0,
            is_default: true,
            locked_by: null,
            lock_holder_name: null,
            thumbnail_url: null,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    await expect.poll(() => capturedRequests.length).toBeGreaterThan(0)

    // conditionalRequests=false → If-None-Match must NOT be sent (thumbnail_url is volatile)
    expect(capturedRequests[0].headers['if-none-match']).toBeUndefined()

    // ETag returned by server should be stored in localStorage
    await expect.poll(() => readSequenceListCache(page, projectId)).not.toBeNull()
    const cachedRaw = await readSequenceListCache(page, projectId)
    if (cachedRaw) {
      const cached = JSON.parse(cachedRaw) as { etag?: string }
      expect(cached.etag).toBe('W/"sequences-etag-v1"')
    }
  })

  test('sequences list never sends If-None-Match (conditionalRequests=false)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId
    const capturedHeaders: Array<Record<string, string>> = []

    await page.route(new RegExp(`/api/projects/${projectId}/sequences$`), async (route) => {
      const req = route.request()
      capturedHeaders.push(req.headers())

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"sequences-etag-v1"' },
        body: JSON.stringify([
          {
            id: mock.sequenceId,
            name: 'Main Sequence',
            version: 1,
            duration_ms: 0,
            is_default: true,
            locked_by: null,
            lock_holder_name: null,
            thumbnail_url: 'https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Signature=aaaa',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)
    await expect.poll(() => capturedHeaders.length).toBeGreaterThan(0)

    // Even with a cached ETag, sequences must never send If-None-Match
    // because thumbnail_url is excluded from ETag hash (GCS re-signing)
    expect(capturedHeaders.every((h) => h['if-none-match'] === undefined)).toBe(true)
  })

  test('sequences validatePayload: cache with expired thumbnail URL is dropped', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    const staleThumbnailUrl =
      'https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Date=20200101T000000Z&X-Goog-Expires=3600'

    // Seed stale cache with expired thumbnail_url
    await page.addInitScript(
      ({ key, value }) => localStorage.setItem(key, value),
      {
        key: sequenceListCacheKey(projectId),
        value: JSON.stringify({
          schemaVersion: SCHEMA_VERSION,
          etag: 'W/"stale-seq-etag"',
          payload: [
            {
              id: 'seq-stale-1',
              name: 'Stale Sequence',
              version: 1,
              duration_ms: 0,
              is_default: true,
              locked_by: null,
              lock_holder_name: null,
              thumbnail_url: staleThumbnailUrl,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ],
          fetchedAt: Date.now() - 60_000,
          expiresAt: Date.now() + 86_400_000,
        }),
      }
    )

    let serverRequested = false
    await page.route(new RegExp(`/api/projects/${projectId}/sequences$`), async (route) => {
      serverRequested = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"fresh-seq-etag"' },
        body: JSON.stringify([
          {
            id: mock.sequenceId,
            name: 'Main Sequence',
            version: 1,
            duration_ms: 0,
            is_default: true,
            locked_by: null,
            lock_holder_name: null,
            thumbnail_url: null,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    // Expired thumbnail cache must be dropped → server must be called
    await expect.poll(() => serverRequested).toBe(true)
  })
})
