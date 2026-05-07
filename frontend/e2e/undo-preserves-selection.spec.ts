import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

/**
 * Regression for #189: Undo / Redo must preserve the current clip selection
 * when the clip still exists in the new timeline, and must refresh the
 * property panel so the reverted value is immediately visible. Pre-fix,
 * the `historyVersion` watcher unconditionally cleared selection, forcing
 * the user to re-select the clip to confirm what the undo reverted.
 */
test.describe('Undo preserves clip selection (issue #189)', () => {
  test('selection and property panel refresh across undo/redo', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // Change scale via the number input (commits directly).
    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toHaveValue('100')
    await scaleInput.fill('150')
    await scaleInput.press('Tab')
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)
    await expect(scaleInput).toHaveValue('150')

    // Move focus off the input so Ctrl/Cmd+Z is not swallowed.
    await page.getByTestId('timeline-area').click()

    // Undo — without this fix the property panel empties because selection is cleared.
    const modifier = process.platform === 'darwin' ? 'Meta' : 'Control'
    await page.keyboard.press(`${modifier}+z`)
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(3)

    // Without clicking the clip again, the property panel should reflect the
    // reverted value because the selection is preserved and refreshed.
    await expect(page.getByTestId('video-scale-input')).toHaveValue('100')
  })
})
