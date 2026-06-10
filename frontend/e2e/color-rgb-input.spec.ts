/**
 * E2E test for Issue #176: Color RGB linked-channel input
 *
 * Verifies that:
 * 1. RGB channel inputs are visible in the property panel for a text clip.
 * 2. Changing R channel (linked OFF) updates only R in the submitted sequence.
 * 3. Changing R channel (linked ON) propagates the delta to G and B as well.
 */
import { expect, test } from '@playwright/test'
import type { Clip } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

/** Build a minimal text clip seeded into layer-1 */
function buildTextClip(id: string): Clip {
  return {
    id,
    asset_id: null,
    text_content: 'Test',
    text_style: {
      fontFamily: 'Noto Sans JP',
      fontSize: 48,
      fontWeight: 'normal',
      fontStyle: 'normal',
      color: '#64c8ff',        // R=100, G=200, B=255
      backgroundColor: '#000000',
      backgroundOpacity: 0,
      textAlign: 'center',
      verticalAlign: 'middle',
      lineHeight: 1.4,
      letterSpacing: 0,
      strokeColor: '#000000',
      strokeWidth: 0,
    },
    start_ms: 0,
    duration_ms: 5000,
    in_point_ms: 0,
    out_point_ms: null,
    transform: { x: 640, y: 360, width: null, height: null, scale: 1, rotation: 0 },
    effects: { opacity: 1 },
  }
}

test.describe('Color RGB linked-channel input (issue #176)', () => {
  test('RGB channel inputs are rendered in the property panel', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = [buildTextClip('text-clip-1')]
    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [buildTextClip('text-clip-1')]

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await expect(clip).toBeVisible()
    await clip.click()

    // Confirm the RGB inputs for text color appear
    await expect(page.getByTestId('color-rgb-r').first()).toBeVisible()
    await expect(page.getByTestId('color-rgb-g').first()).toBeVisible()
    await expect(page.getByTestId('color-rgb-b').first()).toBeVisible()
    await expect(page.getByTestId('color-linked-checkbox').first()).toBeVisible()
  })

  test('R channel change (linked OFF) updates only R in committed color', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = [buildTextClip('text-clip-1')]
    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [buildTextClip('text-clip-1')]

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // linked checkbox is unchecked by default
    const linkedCheckbox = page.getByTestId('color-linked-checkbox').first()
    await expect(linkedCheckbox).not.toBeChecked()

    // Change R to 200 (current: 100)
    const rInput = page.getByTestId('color-rgb-r').first()
    await rInput.fill('200')
    await rInput.press('Enter')

    // Wait for sequence update
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBeGreaterThan(0)

    const lastUpdate = mock.calls.sequenceUpdates[mock.calls.sequenceUpdates.length - 1]
    const committedClip = lastUpdate.timelineData.layers[0].clips[0]
    const committedColor = committedClip.text_style?.color ?? ''

    // R should be ~200 (0xc8), G stays ~200 (0xc8), B stays ~255 (0xff)
    // Original: #64c8ff → R=100(0x64), G=200(0xc8), B=255(0xff)
    // After R→200: #c8c8ff
    expect(committedColor.toLowerCase()).toBe('#c8c8ff')
  })

  test('R channel change (linked ON) propagates delta to G and B', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = [buildTextClip('text-clip-2')]
    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [buildTextClip('text-clip-2')]

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // Enable linked mode
    const linkedCheckbox = page.getByTestId('color-linked-checkbox').first()
    await linkedCheckbox.check()
    await expect(linkedCheckbox).toBeChecked()

    // Change R from 100 to 120 → delta = +20
    // Original: #64c8ff → R=100, G=200, B=255
    // Expected: R=120, G=220, B=255(clamped)
    const rInput = page.getByTestId('color-rgb-r').first()
    await rInput.fill('120')
    await rInput.press('Enter')

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBeGreaterThan(0)

    const lastUpdate = mock.calls.sequenceUpdates[mock.calls.sequenceUpdates.length - 1]
    const committedClip = lastUpdate.timelineData.layers[0].clips[0]
    const committedColor = committedClip.text_style?.color ?? ''

    // R=120(0x78), G=220(0xdc), B=255(clamped 275→255, 0xff)
    expect(committedColor.toLowerCase()).toBe('#78dcff')
  })
})
