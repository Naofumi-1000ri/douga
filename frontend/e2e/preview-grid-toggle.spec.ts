/**
 * E2E: Preview grid / center-line overlay toggle (issue #181)
 *
 * Verifies:
 * 1. Grid overlay is hidden by default.
 * 2. Clicking the "Grid" toggle button shows the overlay (data-testid="preview-grid-overlay").
 * 3. Clicking it again hides the overlay.
 * 4. The preference is persisted in localStorage so it survives a page reload.
 */

import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

test.describe('Preview grid overlay toggle (#181)', () => {
  test('grid is hidden by default and can be toggled on/off', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const previewContainer = page.getByTestId('preview-container')
    await expect(previewContainer).toBeVisible()

    // 1. Grid overlay should NOT be visible by default
    await expect(page.getByTestId('preview-grid-overlay')).toHaveCount(0)

    // 2. Hover over preview container to reveal the toolbar (showPreviewControls)
    await previewContainer.hover()
    await page.waitForTimeout(300)

    // 3. Click the Grid toggle button
    const gridToggle = page.getByTestId('preview-grid-toggle')
    await expect(gridToggle).toBeVisible({ timeout: 5000 })
    await gridToggle.click()
    await page.waitForTimeout(200)

    // Grid overlay should now be visible
    await expect(page.getByTestId('preview-grid-overlay')).toBeVisible()
    // Center lines should also be present
    await expect(page.getByTestId('preview-center-v')).toBeVisible()
    await expect(page.getByTestId('preview-center-h')).toBeVisible()

    // 4. Click toggle again to hide
    await gridToggle.click()
    await page.waitForTimeout(200)
    await expect(page.getByTestId('preview-grid-overlay')).toHaveCount(0)
  })

  test('grid preference persists in localStorage across reload', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const previewContainer = page.getByTestId('preview-container')
    await expect(previewContainer).toBeVisible()

    // Enable grid
    await previewContainer.hover()
    await page.waitForTimeout(300)
    const gridToggle = page.getByTestId('preview-grid-toggle')
    await expect(gridToggle).toBeVisible({ timeout: 5000 })
    await gridToggle.click()
    await page.waitForTimeout(200)
    await expect(page.getByTestId('preview-grid-overlay')).toBeVisible()

    // Verify localStorage persistence
    const stored = await page.evaluate(() => localStorage.getItem('douga-preview-grid-enabled'))
    expect(stored).toBe('true')

    // Reload page — grid should still be enabled
    await page.reload()
    await expect(page.getByTestId('preview-container')).toBeVisible({ timeout: 10000 })
    // After reload, grid should be rendered because localStorage='true'
    await expect(page.getByTestId('preview-grid-overlay')).toBeVisible({ timeout: 5000 })
  })
})
