import { test, expect } from '../fixtures/editor-fixture'

test.describe('Timeline Visual', () => {
  test('timeline initial state', async ({ editorPage }) => {
    await editorPage.resetHoverState()

    await expect(editorPage.timelineArea).toHaveScreenshot('timeline-initial.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })

  test('timeline clip selection highlight', async ({ editorPage }) => {
    const selected = await editorPage.selectFirstClip()
    if (!selected) {
      test.skip(true, 'No clips available to select')
      return
    }

    await editorPage.resetHoverState()

    await expect(editorPage.timelineArea).toHaveScreenshot('timeline-clip-selected.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })

  test('timeline after playhead move', async ({ editorPage }) => {
    await editorPage.movePlayhead(200)
    await editorPage.resetHoverState()

    await expect(editorPage.timelineArea).toHaveScreenshot('timeline-playhead-moved.png', {
      maxDiffPixels: 100,
      threshold: 0.2,
    })
  })
})
