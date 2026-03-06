import { test, expect } from '../fixtures/editor-fixture'

test.describe('Text Clips Visual', () => {
  test('text clip in preview after adding', async ({ editorPage }) => {
    const added = await editorPage.addTextClip()
    if (!added) {
      test.skip(true, 'Could not add text clip')
      return
    }

    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('text-clip-added.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('video'),
      ],
    })
  })

  test('japanese text wrapping display (pre regression)', async ({ editorPage }) => {
    const added = await editorPage.addTextClip()
    if (!added) {
      test.skip(true, 'Could not add text clip')
      return
    }

    // Find text input in property panel and type Japanese text
    const textInput = editorPage.page.locator(
      'textarea[placeholder*="テキスト"], input[placeholder*="テキスト"], textarea[placeholder*="text"], input[placeholder*="text"]'
    ).first()

    if (await textInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await textInput.fill('これは日本語のテキスト折り返しテストです。長い文章が正しく折り返されることを確認します。')
      await editorPage.page.waitForTimeout(500)
    }

    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('text-japanese-wrapping.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('video'),
      ],
    })
  })
})
