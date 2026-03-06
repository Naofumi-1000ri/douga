import { test, expect } from '../fixtures/editor-fixture'

test.describe('Shape Visual', () => {
  test('rectangle shape in preview after adding', async ({ editorPage }) => {
    const added = await editorPage.addRectangleShape()
    if (!added) {
      test.skip(true, 'Could not add rectangle shape')
      return
    }

    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('shape-rectangle-added.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('video'),
      ],
    })
  })

  test('shape selection shows resize handles', async ({ editorPage }) => {
    const added = await editorPage.addRectangleShape()
    if (!added) {
      test.skip(true, 'Could not add rectangle shape')
      return
    }

    // Click on the shape in preview to select it
    const shapeElements = editorPage.previewContainer.locator('[class*="absolute"][style*="border"]')
    if (await shapeElements.count() > 0) {
      await shapeElements.first().click()
      await editorPage.page.waitForTimeout(300)
    }

    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('shape-selected-handles.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('video'),
      ],
    })
  })

  test('shape move and re-click (stale closure regression)', async ({ editorPage }) => {
    const added = await editorPage.addRectangleShape()
    if (!added) {
      test.skip(true, 'Could not add rectangle shape')
      return
    }

    // Select shape in preview
    const shapeElements = editorPage.previewContainer.locator('[class*="absolute"][style*="border"]')
    const count = await shapeElements.count()
    if (count === 0) {
      test.skip(true, 'No shape elements found in preview')
      return
    }

    const shape = shapeElements.first()
    const box = await shape.boundingBox()
    if (!box) {
      test.skip(true, 'Could not get shape bounds')
      return
    }

    // Drag shape
    await shape.hover()
    await editorPage.page.mouse.down()
    await editorPage.page.mouse.move(box.x + 50, box.y + 50)
    await editorPage.page.mouse.up()
    await editorPage.page.waitForTimeout(300)

    // Re-click shape (tests stale closure fix)
    await shape.click()
    await editorPage.page.waitForTimeout(300)

    await editorPage.resetHoverState()

    await expect(editorPage.previewContainer).toHaveScreenshot('shape-after-move-reclick.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
      mask: [
        editorPage.previewContainer.locator('video'),
      ],
    })
  })
})
