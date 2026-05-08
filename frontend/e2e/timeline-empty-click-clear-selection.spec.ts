import { test, expect } from '@playwright/test'
import type { AudioTrack } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

/**
 * Issue #211: Clicking the empty area of the timeline canvas clears all
 * clip/layer/audio-track selections.
 *
 * Scenarios verified:
 * 1. Video clip selected → click empty canvas → selection cleared (property panel hides scale input)
 * 2. Layer header selected → click empty canvas → header loses selected class
 * 3. Audio track header selected → click empty canvas → header loses selected class
 * 4. Ruler click does NOT clear selection (e.target !== e.currentTarget guard)
 * 5. Ruler double-click still adds a marker (existing behaviour not broken)
 */

const LAYER_ID = 'layer-1'
const TRACK_ID = 'track-bgm-1'

async function setupEditorWithVideoClip(page: Parameters<typeof bootstrapMockEditorPage>[0]) {
  const mock = await bootstrapMockEditorPage(page)
  await openSeededEditor(page, mock.projectId, mock.sequenceId)

  // Drop the seeded image asset onto the video layer so there is a clip to select
  await dragAssetToVideoLayer(page, {
    assetId: mock.primaryAssetId,
    layerId: LAYER_ID,
    offsetX: 220,
  })
  await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

  return mock
}

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

/**
 * Click directly on the timeline-canvas element itself (not on any child).
 * Dispatches mousedown then click so that onMouseDownCapture resets
 * suppressNextCanvasClick before onClick fires.
 */
async function clickEmptyCanvas(page: Parameters<typeof bootstrapMockEditorPage>[0]) {
  await page.evaluate(() => {
    const canvas = document.querySelector('[data-testid="timeline-canvas"]') as HTMLElement
    if (!canvas) throw new Error('timeline-canvas not found')
    canvas.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
}

test.describe('Timeline empty click clears selection (issue #211)', () => {
  test('video clip selected → clicking empty canvas clears selection', async ({ page }) => {
    await setupEditorWithVideoClip(page)

    // Select the clip
    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // After selection, the property panel should show the scale input
    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toBeVisible()

    // Click directly on the canvas element itself (not on any child)
    await clickEmptyCanvas(page)

    // Property panel should no longer show the video clip scale input
    await expect(scaleInput).toBeHidden()
  })

  test('layer header selected → clicking empty canvas clears layer selection', async ({ page }) => {
    await setupEditorWithVideoClip(page)

    // Click the layer header to select it (uses data-testid added in #211).
    // Click on the layer name area (past w-6 drag handle + w-4 color picker = ~50px from left).
    const layerHeader = page.getByTestId(`timeline-layer-header-${LAYER_ID}`)
    const headerBox = await layerHeader.boundingBox()
    expect(headerBox).not.toBeNull()
    await page.mouse.click(headerBox!.x + 60, headerBox!.y + headerBox!.height / 2)

    // Verify layer is selected via its CSS class (bg-primary-900/50 applied when selected)
    await expect(layerHeader).toHaveClass(/bg-primary-900/)

    // Click directly on the canvas element itself
    await clickEmptyCanvas(page)

    // Layer header should no longer have the selected class
    await expect(layerHeader).not.toHaveClass(/bg-primary-900/)
  })

  test('audio track header selected → clicking empty canvas clears track selection', async ({ page }) => {
    await setupEditorWithAudioTrack(page)

    // Click the audio track header to select it.
    // Click on the left edge (drag handle area) to avoid the Duck / Mute buttons on the right.
    const trackHeader = page.getByTestId(`timeline-audio-track-header-${TRACK_ID}`)
    const trackBox = await trackHeader.boundingBox()
    expect(trackBox).not.toBeNull()
    await page.mouse.click(trackBox!.x + 4, trackBox!.y + trackBox!.height / 2)

    // Verify the track is selected (bg-amber-900/40 applied when selected)
    await expect(trackHeader).toHaveClass(/bg-amber-900/)

    // Click directly on the canvas element itself
    await clickEmptyCanvas(page)

    // Track header should no longer have the selected class
    await expect(trackHeader).not.toHaveClass(/bg-amber-900/)
  })

  test('clicking ruler does NOT clear existing clip selection', async ({ page }) => {
    await setupEditorWithVideoClip(page)

    // Select the clip
    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // Wait for the property panel to appear (confirms selection)
    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toBeVisible()

    // Click the time ruler (data-testid="timeline-ruler", a child of timeline-canvas).
    // Since ruler is a child, e.target !== e.currentTarget → clearAllSelections must NOT fire.
    const ruler = page.getByTestId('timeline-ruler')
    await expect(ruler).toBeVisible()
    const rulerBox = await ruler.boundingBox()
    expect(rulerBox).not.toBeNull()
    await page.mouse.click(
      rulerBox!.x + 100,
      rulerBox!.y + rulerBox!.height / 2,
    )

    // Selection must still be intact — scale input stays visible
    await expect(scaleInput).toBeVisible()
  })

  test('ruler double-click opens add-marker dialog (existing behaviour unchanged)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // The ruler has data-testid="timeline-ruler"; dblclick on it to open the marker dialog.
    const ruler = page.getByTestId('timeline-ruler')
    await expect(ruler).toBeVisible()

    const rulerBox = await ruler.boundingBox()
    expect(rulerBox).not.toBeNull()

    await page.mouse.dblclick(
      rulerBox!.x + 100,
      rulerBox!.y + rulerBox!.height / 2,
    )

    // The marker dialog should appear (it contains a name input and submit button).
    // Check that the overlay background appeared (bg-black/50 absolute inset-0 z-50).
    // The dialog has a heading with the marker add text.
    await expect(page.locator('.bg-black\\/50')).toBeVisible({ timeout: 5000 })

    // Confirm no sequence update has occurred yet (dialog not submitted)
    expect(mock.calls.sequenceUpdates.length).toBe(0)

    // Dismiss the dialog by pressing Escape
    await page.keyboard.press('Escape')
    await expect(page.locator('.bg-black\\/50')).toBeHidden()
  })
})
