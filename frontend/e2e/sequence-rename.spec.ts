import { test, expect } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

test.describe('Sequence Rename (#155)', () => {
  test('can rename a sequence via the rename button in the sequence panel', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAssetPanelOpen: true,
      },
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the Sequences tab in the left panel - buttons show text from i18n
    // The button text is either "シーケンス" (ja) or "Sequences" (en)
    const sequencesTab = page.locator('button').filter({ hasText: /^(シーケンス|Sequences)$/ }).first()
    await sequencesTab.click()

    // The current sequence name should be visible
    await expect(page.getByText('Main Sequence')).toBeVisible()

    // Hover over the sequence item to reveal the action buttons
    const sequenceRow = page.locator('.group').filter({ hasText: 'Main Sequence' }).first()
    await sequenceRow.hover()

    // Click the rename button (pencil/edit icon)
    const renameButton = page.locator('button[title*="名前変更"], button[title*="Rename"]').first()
    await renameButton.click()

    // An input should appear with the current name selected
    const editInput = page.locator('input[type="text"]').first()
    await expect(editInput).toBeVisible()
    await expect(editInput).toHaveValue('Main Sequence')

    // Clear and type new name
    await editInput.fill('Renamed Sequence')
    await editInput.press('Enter')

    // The sequence list should now show the new name
    await expect(page.getByText('Renamed Sequence')).toBeVisible()
    await expect(page.getByText('Main Sequence')).not.toBeVisible()
  })

  test('can cancel rename with Escape key', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAssetPanelOpen: true,
      },
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the Sequences tab
    const sequencesTab = page.locator('button').filter({ hasText: /^(シーケンス|Sequences)$/ }).first()
    await sequencesTab.click()

    await expect(page.getByText('Main Sequence')).toBeVisible()

    // Hover and click rename
    const sequenceRow = page.locator('.group').filter({ hasText: 'Main Sequence' }).first()
    await sequenceRow.hover()

    const renameButton = page.locator('button[title*="名前変更"], button[title*="Rename"]').first()
    await renameButton.click()

    const editInput = page.locator('input[type="text"]').first()
    await expect(editInput).toBeVisible()

    // Type something then press Escape to cancel
    await editInput.fill('Cancelled Name')
    await editInput.press('Escape')

    // The original name should still be visible
    await expect(page.getByText('Main Sequence')).toBeVisible()
    await expect(page.getByText('Cancelled Name')).not.toBeVisible()
  })
})
