import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'

test.describe('Editor Debug Route', () => {
  test('loads seeded project data and keeps verification context after reload', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await page.goto('/debug/editor?issue=%235')
    await page.waitForLoadState('networkidle')

    await expect(page.getByTestId('editor-debug-page')).toBeVisible()
    await expect(page.getByTestId('editor-debug-issue-input')).toHaveValue('#5')
    await expect(page.getByTestId('editor-debug-project-select')).toHaveValue(mock.projectId)
    await expect(page.getByTestId('editor-debug-sequence-select')).toHaveValue(mock.sequenceId)
    await expect(page.getByTestId('editor-debug-open-editor-link')).toHaveAttribute(
      'href',
      `/project/${mock.projectId}/sequence/${mock.sequenceId}`,
    )

    await page.getByTestId('editor-debug-focus-input').fill('cut boundary playback')
    await page.getByTestId('editor-debug-check-reproduce-fix').check()
    await page.getByTestId('editor-debug-notes').fill('Preview looked stable after the fix.')

    await expect(page.getByTestId('editor-debug-summary')).toContainText('Issue: #5')
    await expect(page.getByTestId('editor-debug-summary')).toContainText('[x] Run the issue-specific reproduction')
    await expect(page.getByTestId('editor-debug-summary')).toContainText('Preview looked stable after the fix.')

    await page.reload()
    await page.waitForLoadState('networkidle')

    await expect(page.getByTestId('editor-debug-focus-input')).toHaveValue('cut boundary playback')
    await expect(page.getByTestId('editor-debug-check-reproduce-fix')).toBeChecked()
    await expect(page.getByTestId('editor-debug-notes')).toHaveValue('Preview looked stable after the fix.')
  })
})
