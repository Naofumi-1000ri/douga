import { test, expect } from '../fixtures/editor-fixture'

test.describe('Preview Panel Visual', () => {
  test('preview panel empty state', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('preview-empty.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('canvas'),
        editorPage.previewContainer.locator('video'),
      ],
    })
  })

  test('preview with clip selected shows transform handles', async ({ editorPage }) => {
    const selected = await editorPage.selectFirstClip()
    if (!selected) {
      test.skip(true, 'No clips to select')
      return
    }

    await editorPage.page.waitForTimeout(300)
    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('preview-clip-transform.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('canvas'),
        editorPage.previewContainer.locator('video'),
      ],
    })
  })
})
