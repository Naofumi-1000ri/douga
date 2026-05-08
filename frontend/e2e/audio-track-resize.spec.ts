import { test, expect } from '@playwright/test'
import type { AudioTrack } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

const TRACK_ID = 'track-bgm-1'

async function setupEditorWithAudioTrack(page: Parameters<typeof bootstrapMockEditorPage>[0]) {
  const mock = await bootstrapMockEditorPage(page)

  const bgmTrack: AudioTrack = {
    id: TRACK_ID,
    name: 'BGM 1',
    type: 'bgm',
    volume: 1,
    muted: false,
    visible: true,
    clips: [],
  }

  mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [bgmTrack]
  mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(JSON.stringify([bgmTrack]))

  await openSeededEditor(page, mock.projectId, mock.sequenceId)

  return mock
}

test.describe('Audio track resize', () => {
  test('audio track row has an initial height that can be measured', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    const trackRow = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)
    await expect(trackRow).toBeVisible()

    const box = await trackRow.boundingBox()
    expect(box).not.toBeNull()
    // Default height should be 48px (DEFAULT_LAYER_HEIGHT)
    expect(box!.height).toBeGreaterThanOrEqual(32)
  })

  test('resize handle is present on the audio track row', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    const handle = page.getByTestId(`timeline-audio-track-resize-handle-${TRACK_ID}`)
    await expect(handle).toBeVisible()
  })

  test('dragging resize handle changes audio track row height', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    const trackRow = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)
    const handle = page.getByTestId(`timeline-audio-track-resize-handle-${TRACK_ID}`)

    await expect(trackRow).toBeVisible()
    await expect(handle).toBeVisible()

    const initialBox = await trackRow.boundingBox()
    expect(initialBox).not.toBeNull()
    const initialHeight = initialBox!.height

    // Drag the handle downward by 50px to increase height
    const handleBox = await handle.boundingBox()
    expect(handleBox).not.toBeNull()
    // Use a viewport-safe X coordinate (handle may extend beyond viewport width due to scroll container)
    const viewport = page.viewportSize()
    const safeX = Math.min(handleBox!.x + 100, (viewport?.width ?? 1280) - 10)
    const startY = handleBox!.y + handleBox!.height / 2

    await page.mouse.move(safeX, startY)
    await page.mouse.down()
    await page.mouse.move(safeX, startY + 50, { steps: 10 })
    await page.mouse.up()

    const newBox = await trackRow.boundingBox()
    expect(newBox).not.toBeNull()
    const newHeight = newBox!.height

    // The height should have increased and should differ from the initial (64px) height
    expect(newHeight).toBeGreaterThan(initialHeight)
    // Specifically, should not still be the default 64px
    expect(newHeight).not.toBe(64)
  })

  test('resized audio track height is restored after reload via localStorage', async ({ page }) => {
    const mock = await setupEditorWithAudioTrack(page)

    const handle = page.getByTestId(`timeline-audio-track-resize-handle-${TRACK_ID}`)
    await expect(handle).toBeVisible()

    const handleBox = await handle.boundingBox()
    expect(handleBox).not.toBeNull()
    // Use a viewport-safe X coordinate (handle may extend beyond viewport width due to scroll container)
    const viewport2 = page.viewportSize()
    const safeX2 = Math.min(handleBox!.x + 100, (viewport2?.width ?? 1280) - 10)
    const startY2 = handleBox!.y + handleBox!.height / 2

    // Drag downward 60px
    await page.mouse.move(safeX2, startY2)
    await page.mouse.down()
    await page.mouse.move(safeX2, startY2 + 60, { steps: 10 })
    await page.mouse.up()

    const trackRow = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)
    const afterResizeBox = await trackRow.boundingBox()
    expect(afterResizeBox).not.toBeNull()
    const heightAfterResize = afterResizeBox!.height

    // The height should differ from the initial 64px (proves resize happened)
    expect(heightAfterResize).not.toBe(64)

    // Reload the page
    await page.reload()
    await page.waitForLoadState('networkidle')
    await page.getByTestId('editor-header').waitFor()

    // Reopen the same editor page
    await page.goto(`/project/${mock.projectId}/sequence/${mock.sequenceId}`)
    await page.waitForLoadState('networkidle')
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()

    const trackRowAfterReload = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)
    await expect(trackRowAfterReload).toBeVisible()

    const reloadedBox = await trackRowAfterReload.boundingBox()
    expect(reloadedBox).not.toBeNull()
    // Should be restored to the resized height, not the default 64px
    expect(reloadedBox!.height).not.toBe(64)
    expect(reloadedBox!.height).toBeCloseTo(heightAfterResize, 1)
  })

  test('header height matches clip row height (no vertical misalignment)', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    const header = page.getByTestId(`timeline-audio-track-header-${TRACK_ID}`)
    const row = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)

    await expect(header).toBeVisible()
    await expect(row).toBeVisible()

    const headerBox = await header.boundingBox()
    const rowBox = await row.boundingBox()
    expect(headerBox).not.toBeNull()
    expect(rowBox).not.toBeNull()

    // Header and clip row must share the same height (old h-16 fixed header would fail this)
    expect(headerBox!.height).toBeCloseTo(rowBox!.height, 1)
  })

  test('header height matches clip row height after resize', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    const headerHandle = page.getByTestId(`timeline-audio-track-header-resize-handle-${TRACK_ID}`)
    const header = page.getByTestId(`timeline-audio-track-header-${TRACK_ID}`)
    const row = page.getByTestId(`timeline-audio-track-row-${TRACK_ID}`)

    await expect(headerHandle).toBeVisible()

    const handleBox = await headerHandle.boundingBox()
    expect(handleBox).not.toBeNull()
    const viewport = page.viewportSize()
    const safeX = Math.min(handleBox!.x + 100, (viewport?.width ?? 1280) - 10)
    const startY = handleBox!.y + handleBox!.height / 2

    // Drag header resize handle downward by 50px
    await page.mouse.move(safeX, startY)
    await page.mouse.down()
    await page.mouse.move(safeX, startY + 50, { steps: 10 })
    await page.mouse.up()

    const headerBox = await header.boundingBox()
    const rowBox = await row.boundingBox()
    expect(headerBox).not.toBeNull()
    expect(rowBox).not.toBeNull()

    // After resize via header handle, both must still be equal
    expect(headerBox!.height).toBeCloseTo(rowBox!.height, 1)
    // And the height must have changed from the default 64px
    expect(headerBox!.height).toBeGreaterThan(64)
  })
})
