import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

test.describe('Editor Critical Path', () => {
  test('adds an asset clip, edits it, and keeps the change after reload', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clipLocator = page.locator('[data-testid^="timeline-video-clip-"]')
    await expect(clipLocator).toHaveCount(0)
    await expect(page.getByTestId('sequence-save-status')).toBeVisible()

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)
    await expect(clipLocator).toHaveCount(1)

    const clip = clipLocator.first()
    await clip.click()

    const scaleInput = page.getByTestId('video-scale-input')
    await expect(scaleInput).toHaveValue('100')
    await scaleInput.fill('150')
    await scaleInput.press('Tab')

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)
    await expect.poll(() => mock.sequences[mock.sequenceId].version).toBe(3)

    await expect(page.locator(`[data-asset-id="${mock.primaryAssetId}"]`)).toBeVisible()

    await page.reload()
    await page.waitForLoadState('networkidle')
    await page.getByTestId('editor-header').waitFor()

    await expect(clipLocator).toHaveCount(1)
    await clipLocator.first().click()
    await expect(page.getByTestId('video-scale-input')).toHaveValue('150')
  })

  test('opens lazy editor panels on demand without breaking the editor shell', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAIChatOpen: false,
        isPropertyPanelOpen: false,
      },
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('editor-property-rail').click()
    await expect(page.getByTestId('right-panel')).toBeVisible()

    await page.getByTestId('editor-ai-toggle').click()
    await expect(page.getByTestId('ai-chat-panel')).toBeVisible()
    await expect(page.getByTestId('ai-chat-input')).toBeVisible()

    await page.getByTestId('editor-activity-rail').click()
    await expect(page.getByTestId('activity-panel')).toBeVisible()
    await expect(page.getByText('No activity yet')).toBeVisible()
  })
})
