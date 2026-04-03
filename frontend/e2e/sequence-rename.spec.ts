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

    // Open the Sequences tab in the left panel
    const sequencesTab = page.getByRole('button', { name: 'Sequences' })
    await sequencesTab.click()

    // Find the sequence row by data-testid
    const sequenceRow = page.getByTestId(`sequence-row-${mock.sequenceId}`)
    await expect(sequenceRow).toBeVisible()

    // Click the rename button (force click - hidden until hover)
    const renameButton = sequenceRow.getByRole('button', { name: 'Rename' })
    await renameButton.click({ force: true })

    // An input (textbox) should appear with the current name
    const editInput = sequenceRow.getByRole('textbox')
    await expect(editInput).toBeVisible()
    await expect(editInput).toHaveValue('Main Sequence')

    // Clear and type new name
    await editInput.fill('Renamed Sequence')
    await editInput.press('Enter')

    // The sequence list should now show the new name
    await expect(sequenceRow.getByText('Renamed Sequence')).toBeVisible()
  })

  test('can cancel rename with Escape key', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAssetPanelOpen: true,
      },
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the Sequences tab
    const sequencesTab = page.getByRole('button', { name: 'Sequences' })
    await sequencesTab.click()

    const sequenceRow = page.getByTestId(`sequence-row-${mock.sequenceId}`)
    await expect(sequenceRow).toBeVisible()

    // Click rename button (force click - hidden until hover)
    const renameButton = sequenceRow.getByRole('button', { name: 'Rename' })
    await renameButton.click({ force: true })

    const editInput = sequenceRow.getByRole('textbox')
    await expect(editInput).toBeVisible()

    // Type something then press Escape to cancel
    await editInput.fill('Cancelled Name')
    await editInput.press('Escape')

    // The original name should still be visible, not the cancelled one
    await expect(sequenceRow.getByText('Main Sequence')).toBeVisible()
    await expect(page.getByText('Cancelled Name')).not.toBeVisible()
  })
})
