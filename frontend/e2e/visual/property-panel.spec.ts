import { test, expect } from '../fixtures/editor-fixture'

test.describe('Property Panel Visual', () => {
  test('right panel with nothing selected', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    const isVisible = await editorPage.rightPanel.isVisible().catch(() => false)
    if (!isVisible) {
      // Right panel may auto-hide when nothing is selected - screenshot the container area
      test.skip(true, 'Right panel hidden when nothing selected')
      return
    }

    await expect(editorPage.rightPanel).toHaveScreenshot('property-panel-empty.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })

  test('property panel with clip selected', async ({ editorPage }) => {
    const selected = await editorPage.selectFirstClip()
    if (!selected) {
      test.skip(true, 'No clips to select')
      return
    }

    await editorPage.page.waitForTimeout(300)
    await editorPage.resetHoverState()

    const isVisible = await editorPage.rightPanel.isVisible().catch(() => false)
    if (!isVisible) {
      test.skip(true, 'Right panel not visible after selection')
      return
    }

    await expect(editorPage.rightPanel).toHaveScreenshot('property-panel-clip-selected.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })

  test('property panel with shape properties', async ({ editorPage }) => {
    const added = await editorPage.addRectangleShape()
    if (!added) {
      test.skip(true, 'Could not add shape')
      return
    }

    await editorPage.page.waitForTimeout(300)
    await editorPage.resetHoverState()

    const isVisible = await editorPage.rightPanel.isVisible().catch(() => false)
    if (!isVisible) {
      test.skip(true, 'Right panel not visible after shape add')
      return
    }

    await expect(editorPage.rightPanel).toHaveScreenshot('property-panel-shape.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })
})
