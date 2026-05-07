import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

/**
 * Regression for #184: property changes driven by slider drag
 * (onChange → updateTimelineLocal + onMouseUp → commit) must be undoable.
 *
 * Pre-fix, updateTimelineLocal mutated the store to the in-progress value,
 * so by the time the commit ran, the "before" snapshot pushed to the undo
 * history was equal to the "after" — making Ctrl/Cmd+Z a no-op.
 */
test.describe('Undo property change (issue #184)', () => {
  test('slider-driven opacity change is reverted by undo', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Seed a video clip so the property panel is available.
    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    const opacityInput = page.getByTestId('video-opacity-input')
    await expect(opacityInput).toHaveValue('100')

    // Simulate a slider drag: input events drive onChange (→ updateTimelineLocal),
    // then a mouseup drives the commit (→ saveSequence). React listens to the
    // native 'input' event for range inputs, so we dispatch it directly.
    const slider = page.getByTestId('video-opacity-slider')
    await slider.evaluate((el: HTMLInputElement) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(el, '0.8')
      el.dispatchEvent(new Event('input', { bubbles: true }))
      setter.call(el, '0.5')
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await slider.dispatchEvent('mouseup')

    // Commit reached the server: new sequence update recorded.
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)
    const committedClip = mock.calls.sequenceUpdates[1].timelineData.layers[0].clips[0]
    expect(committedClip.effects.opacity).toBeCloseTo(0.5, 2)

    // Move focus off the slider so the keyboard handler accepts Ctrl/Cmd+Z.
    await page.getByTestId('timeline-area').click()

    // Undo the opacity change.
    const modifier = process.platform === 'darwin' ? 'Meta' : 'Control'
    await page.keyboard.press(`${modifier}+z`)

    // Re-select to refresh the property panel.
    await clip.click()
    await expect(page.getByTestId('video-opacity-input')).toHaveValue('100')

    // The undo produced a new sequence update returning opacity to 1.0.
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBeGreaterThanOrEqual(3)
    const restoredUpdate = mock.calls.sequenceUpdates[mock.calls.sequenceUpdates.length - 1]
    const restoredClip = restoredUpdate.timelineData.layers[0].clips[0]
    expect(restoredClip.effects.opacity).toBeCloseTo(1.0, 2)
  })
})
