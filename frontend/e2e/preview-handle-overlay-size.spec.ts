/**
 * Regression test for PR #332 review finding (issue #219 follow-up):
 *
 * The detached handle overlay (zIndex:1000 sibling of the clip body) has no
 * in-flow child, so its `relative` wrapper collapses to 0x0 unless an explicit
 * width/height is set. When that happened, all resize/rotate/crop handles
 * resolved their percentage positions against a 0x0 box and stacked at the
 * clip-center origin, making them unusable.
 *
 * Reproduction conditions covered here:
 * 1. Image clip with transform.width/height = null (state right after the clip
 *    is added, before any resize) — the clip body is sized by the <img>'s
 *    natural size, which the overlay cannot inherit.
 * 2. Video clip with chroma key enabled — the <video> becomes position:absolute
 *    and the ChromaKeyCanvas sizes the body, again not inheritable by the overlay.
 */

import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'
import type { Clip } from '../src/store/projectStore'

// 320x180 solid-green 1s VP8 WebM (generated with ffmpeg lavfi color source).
// Small enough to inline; loads instantly as a data URL in chromium.
const GREEN_WEBM_DATA_URL =
  'data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAN/EU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEkTbuMU6uEHFO7a1OsggNp7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjEuNy4xMDBXQYxMYXZmNjEuNy4xMDBEiYhAj0AAAAAAABZUrmvJrgEAAAAAAABA14EBc8WIHyvvNZMYzGycgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QF9eEA4JGwggFAuoG0moECVbCEVbmBARJUw2f7c3OfY8CAZ8iZRaOHRU5DT0RFUkSHjExhdmY2MS43LjEwMHNz1mPAi2PFiB8r7zWTGMxsZ8ihRaOHRU5DT0RFUkSHlExhdmM2MS4xOS4xMDEgbGlidnB4Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMS4wMDAwMDAwMDAAH0O2dUG/54EAo0CjgQAAgNAPAJ0BKkABtAAARwiFhYiFhIgCAgJ1qgP4A/oCBrak9waBZJ9r25snOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnOHsnLgD+/W7z/+K1h1nF/4rf/9Nk8VeM/+mLAKOdgQBkALECAAEQEAAYABhYL/QACIAEM1+tck+VgACjnYEAyACxAgABEBAAGAAYWC/0AAiABDNfrXJPlYAAo52BASwAsQIAARAQABgAGFgv9AAIgAQzX61yT5WAAKOdgQGQALECAAEQEAAYABhYL/QACIAEM1+tck+VgACjnYEB9ACxAgABEBAAGAAYWC/0AAiABDNfrXJPlYAAo52BAlgAsQIAARAQABgAGFgv9AAIgAQzX61yT5WAAKOcgQK8AJECAAEQEBRgAGFgv9AAIgAQzX61yT5WAKOdgQMgALECAAEQEAAYABhYL/QACIAEM1+tck+VgACjnYEDhACxAgABEBAAGAAYWC/0AAiABDNfrXJPlYAAHFO7a5G7j7OBALeK94EB8YIBpPCBAw=='

function buildMediaClip(id: string, assetId: string, overrides: Partial<Clip> = {}): Clip {
  return {
    id,
    asset_id: assetId,
    start_ms: 0,
    duration_ms: 5000,
    in_point_ms: 0,
    out_point_ms: null,
    transform: {
      x: 0,
      y: 0,
      // Both null — the exact state right after a clip is added (no explicit resize yet)
      width: null,
      height: null,
      scale: 1,
      rotation: 0,
    },
    effects: {
      opacity: 1,
    },
    ...overrides,
  }
}

test.describe('Preview handle overlay sizing (#219 review follow-up)', () => {
  test('image clip with null transform size: handles surround the image, not stacked at origin', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    // Mock primary asset is a 1280x720 SVG image — same as the project canvas,
    // so the image fills the stage exactly when x=0/y=0/scale=1.
    const imageClip = buildMediaClip('clip-img-natural', mock.primaryAssetId)

    const sequence = mock.sequences[mock.sequenceId]
    sequence.timeline_data.duration_ms = 5000
    sequence.timeline_data.layers[0].clips = [imageClip]
    mock.projectDetails[mock.projectId].timeline_data = JSON.parse(
      JSON.stringify(sequence.timeline_data)
    )

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const previewContainer = page.getByTestId('preview-container')
    await expect(previewContainer).toBeVisible()

    // Select the clip via the timeline
    const timelineClip = page.getByTestId('timeline-video-clip-clip-img-natural')
    await expect(timelineClip).toBeVisible({ timeout: 10000 })
    await timelineClip.click()
    await page.waitForTimeout(300)

    // The handle overlay must exist and match the image's natural size on screen
    const overlay = page.getByTestId('preview-handle-overlay')
    await expect(overlay).toBeAttached({ timeout: 5000 })

    const stageLocator = previewContainer.locator('[class*="origin-top-left"]').first()
    const stageBox = await stageLocator.boundingBox()
    expect(stageBox).not.toBeNull()
    if (!stageBox) return

    // Wait until ResizeObserver propagates the measured size to the overlay
    await expect.poll(async () => {
      const box = await overlay.boundingBox()
      return box ? Math.round(box.width) : 0
    }, { timeout: 5000 }).toBeGreaterThan(Math.round(stageBox.width * 0.9))

    const overlayBox = await overlay.boundingBox()
    expect(overlayBox).not.toBeNull()
    if (!overlayBox) return

    // Overlay box should cover the whole stage (image is 1280x720 = canvas size)
    expect(Math.abs(overlayBox.width - stageBox.width)).toBeLessThan(10)
    expect(Math.abs(overlayBox.height - stageBox.height)).toBeLessThan(10)

    // The bottom-right resize handle must sit at the image's bottom-right corner
    // (with the bug, every handle collapsed to the clip center)
    const brHandle = page.getByTestId('preview-image-resize-br')
    await expect(brHandle).toBeVisible()
    const brBox = await brHandle.boundingBox()
    expect(brBox).not.toBeNull()
    if (!brBox) return

    const brCenterX = brBox.x + brBox.width / 2
    const brCenterY = brBox.y + brBox.height / 2
    const stageRight = stageBox.x + stageBox.width
    const stageBottom = stageBox.y + stageBox.height

    expect(Math.abs(brCenterX - stageRight)).toBeLessThan(15)
    expect(Math.abs(brCenterY - stageBottom)).toBeLessThan(15)

    // Rotate handle must be above the image's top-center, not at the center
    const rotateHandle = page.getByTestId('preview-rotate-handle')
    await expect(rotateHandle).toBeVisible()
    const rotateBox = await rotateHandle.boundingBox()
    expect(rotateBox).not.toBeNull()
    if (!rotateBox) return

    const rotateCenterX = rotateBox.x + rotateBox.width / 2
    const rotateCenterY = rotateBox.y + rotateBox.height / 2
    const stageCenterX = stageBox.x + stageBox.width / 2

    expect(Math.abs(rotateCenterX - stageCenterX)).toBeLessThan(15)
    // Above the stage top edge (handle offset is -40px in clip space)
    expect(rotateCenterY).toBeLessThan(stageBox.y + 10)
  })

  test('chroma-key video clip: handle overlay matches the video size instead of collapsing to 0x0', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    // Register a real (data URL) WebM video asset so <video> can load metadata
    // and the ChromaKeyCanvas can size the clip body.
    const videoAssetId = 'asset-video-green'
    mock.assetsByProject[mock.projectId].push({
      id: videoAssetId,
      project_id: mock.projectId,
      name: 'Green Screen',
      type: 'video',
      subtype: undefined,
      storage_key: 'mock/green.webm',
      storage_url: GREEN_WEBM_DATA_URL,
      thumbnail_url: null,
      duration_ms: 1000,
      width: 320,
      height: 180,
      file_size: 1000,
      mime_type: 'video/webm',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    })

    // duration 5000ms keeps the timeline clip wide enough that trim handles
    // do not cover its center (clicking the center selects the clip).
    const videoClip = buildMediaClip('clip-video-chroma', videoAssetId, {
      effects: {
        opacity: 1,
        chroma_key: {
          enabled: true,
          color: '#00FF00',
          similarity: 0.1,
          blend: 0,
        },
      },
    })

    const sequence = mock.sequences[mock.sequenceId]
    sequence.timeline_data.duration_ms = 5000
    sequence.timeline_data.layers[0].clips = [videoClip]
    mock.projectDetails[mock.projectId].timeline_data = JSON.parse(
      JSON.stringify(sequence.timeline_data)
    )

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const previewContainer = page.getByTestId('preview-container')
    await expect(previewContainer).toBeVisible()

    // Select the clip via the timeline
    const timelineClip = page.getByTestId('timeline-video-clip-clip-video-chroma')
    await expect(timelineClip).toBeVisible({ timeout: 10000 })
    await timelineClip.click()
    await page.waitForTimeout(300)

    const overlay = page.getByTestId('preview-handle-overlay')
    await expect(overlay).toBeAttached({ timeout: 5000 })

    const stageLocator = previewContainer.locator('[class*="origin-top-left"]').first()
    const stageBox = await stageLocator.boundingBox()
    expect(stageBox).not.toBeNull()
    if (!stageBox) return

    // Expected on-screen clip size: 320x180 in canvas space, scaled by stage scale
    const stageScale = stageBox.width / 1280
    const expectedWidth = 320 * stageScale
    const expectedHeight = 180 * stageScale

    // Wait for <video> metadata + ChromaKeyCanvas sizing + ResizeObserver propagation
    await expect.poll(async () => {
      const box = await overlay.boundingBox()
      return box ? Math.round(box.width) : 0
    }, { timeout: 10000 }).toBeGreaterThan(Math.round(expectedWidth * 0.9))

    const overlayBox = await overlay.boundingBox()
    expect(overlayBox).not.toBeNull()
    if (!overlayBox) return

    expect(Math.abs(overlayBox.width - expectedWidth)).toBeLessThan(10)
    expect(Math.abs(overlayBox.height - expectedHeight)).toBeLessThan(10)

    // Rotate handle must be horizontally centered on the clip and above its top edge
    const rotateHandle = page.getByTestId('preview-rotate-handle')
    await expect(rotateHandle).toBeVisible()
    const rotateBox = await rotateHandle.boundingBox()
    expect(rotateBox).not.toBeNull()
    if (!rotateBox) return

    const rotateCenterX = rotateBox.x + rotateBox.width / 2
    const rotateCenterY = rotateBox.y + rotateBox.height / 2
    const clipCenterX = stageBox.x + stageBox.width / 2
    const clipTopY = stageBox.y + stageBox.height / 2 - expectedHeight / 2

    expect(Math.abs(rotateCenterX - clipCenterX)).toBeLessThan(15)
    // Above the clip top edge (with the bug it stacked at the clip center)
    expect(rotateCenterY).toBeLessThan(clipTopY + 5)
  })
})
