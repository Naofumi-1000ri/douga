import { test, expect, type Page, type Route } from '@playwright/test'
import { EditorPage } from './page-objects/EditorPage'

const PROJECT_ID = 'preview-cut-project'
const SEQUENCE_ID = 'preview-cut-sequence'
const ASSET_ID = 'preview-cut-asset'
const TIMELINE_DURATION_MS = 2000
const EDITOR_LAYOUT_STORAGE_KEY = 'douga-editor-layout'

type TimelineScenario = 'adjacent-cut' | 'gap-at-boundary'

const asset = {
  id: ASSET_ID,
  project_id: PROJECT_ID,
  name: 'preview-cut.mp4',
  type: 'video',
  storage_key: 'preview-cut.mp4',
  storage_url: '/lp/lp_video.mp4',
  thumbnail_url: null,
  duration_ms: TIMELINE_DURATION_MS,
  width: 1920,
  height: 1080,
  file_size: 1024,
  mime_type: 'video/mp4',
  folder_id: null,
  created_at: '2026-03-08T00:00:00Z',
}

function buildTimelineData(scenario: TimelineScenario) {
  const secondClipStartMs = scenario === 'adjacent-cut' ? 1000 : 1100

  return {
    version: '1.0',
    duration_ms: TIMELINE_DURATION_MS,
    layers: [
      {
        id: 'layer-1',
        name: 'Content',
        order: 0,
        visible: true,
        locked: false,
        clips: [
          {
            id: 'clip-a',
            asset_id: ASSET_ID,
            start_ms: 0,
            duration_ms: 1000,
            in_point_ms: 0,
            out_point_ms: 1000,
            speed: 1,
            freeze_frame_ms: 0,
            transform: {
              x: 0,
              y: 0,
              width: null,
              height: null,
              scale: 1,
              rotation: 0,
            },
            effects: {
              opacity: 1,
            },
          },
          {
            id: 'clip-b',
            asset_id: ASSET_ID,
            start_ms: secondClipStartMs,
            duration_ms: 900,
            in_point_ms: 1000,
            out_point_ms: 1900,
            speed: 1,
            freeze_frame_ms: 0,
            transform: {
              x: 0,
              y: 0,
              width: null,
              height: null,
              scale: 1,
              rotation: 0,
            },
            effects: {
              opacity: 1,
            },
          },
        ],
      },
    ],
    audio_tracks: [],
  }
}

function buildProject(timelineData: ReturnType<typeof buildTimelineData>) {
  return {
    id: PROJECT_ID,
    name: 'Preview Cut Boundary',
    description: null,
    status: 'draft',
    duration_ms: TIMELINE_DURATION_MS,
    thumbnail_url: null,
    created_at: '2026-03-08T00:00:00Z',
    updated_at: '2026-03-08T00:00:00Z',
    user_id: 'dev-user',
    width: 1920,
    height: 1080,
    fps: 30,
    timeline_data: timelineData,
    ai_provider: null,
    version: 1,
  }
}

function buildSequence(timelineData: ReturnType<typeof buildTimelineData>) {
  return {
    id: SEQUENCE_ID,
    project_id: PROJECT_ID,
    name: 'Issue #4 Sequence',
    timeline_data: timelineData,
    version: 1,
    duration_ms: TIMELINE_DURATION_MS,
    is_default: true,
    locked_by: 'dev-user',
    lock_holder_name: 'Dev User',
    locked_at: '2026-03-08T00:00:00Z',
    created_at: '2026-03-08T00:00:00Z',
    updated_at: '2026-03-08T00:00:00Z',
  }
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function seedEditorLayout(page: Page, playheadPosition: number) {
  await page.addInitScript(
    ({ storageKey, position }) => {
      window.localStorage.setItem(storageKey, JSON.stringify({
        previewHeight: 400,
        leftPanelWidth: 288,
        rightPanelWidth: 288,
        aiPanelWidth: 320,
        activityPanelWidth: 320,
        isAIChatOpen: true,
        isPropertyPanelOpen: true,
        isAssetPanelOpen: true,
        playheadPosition: position,
        isSyncEnabled: true,
        previewZoom: 1,
      }))
    },
    { storageKey: EDITOR_LAYOUT_STORAGE_KEY, position: playheadPosition },
  )
}

async function mockEditorApi(page: Page, scenario: TimelineScenario) {
  const timelineData = buildTimelineData(scenario)
  const project = buildProject(timelineData)
  const sequence = buildSequence(timelineData)

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url
    const method = request.method()

    if (!pathname.startsWith('/api/')) {
      await route.continue()
      return
    }

    if (method === 'GET' && pathname === '/api/version') {
      return fulfillJson(route, { git_hash: 'test-git-hash' })
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}`) {
      return fulfillJson(route, project)
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/assets`) {
      return fulfillJson(route, [asset])
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/folders`) {
      return fulfillJson(route, [])
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/assets/${ASSET_ID}/signed-url`) {
      return fulfillJson(route, { url: '/lp/lp_video.mp4', expires_in_seconds: 3600 })
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/assets/${ASSET_ID}/grid-thumbnails`) {
      return fulfillJson(route, {
        thumbnails: {
          0: '/og-image.jpg',
          1000: '/og-image.jpg',
        },
        interval_ms: 1000,
        duration_ms: TIMELINE_DURATION_MS,
        width: 160,
        height: 90,
      })
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/assets/${ASSET_ID}/thumbnail`) {
      return fulfillJson(route, {
        url: '/og-image.jpg',
        time_ms: Number(url.searchParams.get('time_ms') ?? '0'),
        width: Number(url.searchParams.get('width') ?? '64'),
        height: Number(url.searchParams.get('height') ?? '36'),
      })
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/assets/${ASSET_ID}/generate-priority-thumbnails`) {
      return fulfillJson(route, {})
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/sequences/${SEQUENCE_ID}`) {
      return fulfillJson(route, sequence)
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/sequences/${SEQUENCE_ID}/lock`) {
      return fulfillJson(route, {
        locked: true,
        locked_by: 'dev-user',
        lock_holder_name: 'Dev User',
        locked_at: '2026-03-08T00:00:00Z',
        edit_token: 'test-edit-token',
      })
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/sequences/${SEQUENCE_ID}/heartbeat`) {
      return fulfillJson(route, {
        locked: true,
        locked_by: 'dev-user',
        lock_holder_name: 'Dev User',
        locked_at: '2026-03-08T00:00:00Z',
        edit_token: 'test-edit-token',
      })
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/sequences/${SEQUENCE_ID}/unlock`) {
      return fulfillJson(route, {})
    }

    if (method === 'GET' && pathname === `/api/projects/${PROJECT_ID}/render/history`) {
      return fulfillJson(route, [])
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/thumbnail`) {
      return fulfillJson(route, { thumbnail_url: '/og-image.jpg' })
    }

    if (method === 'POST' && pathname === `/api/projects/${PROJECT_ID}/sequences/${SEQUENCE_ID}/thumbnail`) {
      return fulfillJson(route, { thumbnail_url: '/og-image.jpg' })
    }

    throw new Error(`Unhandled API request in preview cut boundary test: ${method} ${pathname}`)
  })
}

test('keeps the upcoming clip video element mounted across adjacent cut boundaries', async ({ page }) => {
  await seedEditorLayout(page, 950)
  await mockEditorApi(page, 'adjacent-cut')

  const editorPage = new EditorPage(page)
  await page.goto(`/project/${PROJECT_ID}/sequence/${SEQUENCE_ID}`)
  await editorPage.waitForReady()

  const nextClipVideo = page.locator('video[data-clip-id="clip-b"]')
  await expect(nextClipVideo).toHaveCount(1)
  await expect.poll(async () => nextClipVideo.evaluate((node) => node.readyState), {
    timeout: 15000,
  }).toBeGreaterThanOrEqual(1)

  const nextClipHandle = await nextClipVideo.elementHandle()
  expect(nextClipHandle).not.toBeNull()
  if (!nextClipHandle) {
    throw new Error('Missing next clip video handle')
  }

  const currentTimeDisplay = editorPage.timelineArea.locator('span.text-white.text-sm.font-mono.cursor-pointer').first()
  await currentTimeDisplay.dblclick()
  const currentTimeInput = editorPage.timelineArea.locator('input[placeholder="00:00.000"]').first()
  await currentTimeInput.fill('00:01.050')
  await currentTimeInput.press('Enter')
  await expect(nextClipVideo).toHaveAttribute('data-active', 'true', { timeout: 5000 })

  const sameNode = await nextClipHandle.evaluate(
    (node, selector) => node.isConnected && node === document.querySelector(selector),
    'video[data-clip-id="clip-b"]',
  )

  expect(sameNode).toBe(true)
})

test('keeps genuine gaps empty and black in the preview', async ({ page }) => {
  await seedEditorLayout(page, 1000)
  await mockEditorApi(page, 'gap-at-boundary')

  const editorPage = new EditorPage(page)
  await page.goto(`/project/${PROJECT_ID}/sequence/${SEQUENCE_ID}`)
  await editorPage.waitForReady()

  await expect(page.locator('video[data-active="true"]')).toHaveCount(0)
  await expect(editorPage.previewContainer.locator('.bg-black.cursor-default').first()).toBeVisible()
  await expect(editorPage.previewContainer).toContainText('0:01.00')
})
