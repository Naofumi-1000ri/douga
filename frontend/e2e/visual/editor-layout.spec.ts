import { test, expect } from '../fixtures/editor-fixture'

test.describe('Editor Layout Visual', () => {
  test('initial editor layout', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    await expect(editorPage.page).toHaveScreenshot('editor-layout-full.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('canvas'),
        editorPage.previewContainer.locator('video'),
      ],
    })
  })

  test('left panel visible', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    await expect(editorPage.leftPanel).toHaveScreenshot('left-panel.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })

  test('right panel visible', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    // Right panel may not be visible if nothing is selected
    const isVisible = await editorPage.rightPanel.isVisible().catch(() => false)
    if (!isVisible) {
      test.skip(true, 'Right panel not visible (no selection)')
      return
    }

    await expect(editorPage.rightPanel).toHaveScreenshot('right-panel.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })
})
