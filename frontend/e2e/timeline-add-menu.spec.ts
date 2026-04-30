import { expect, test } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

test.describe('timeline +Add menu submenu structure (#192)', () => {
  test('Shapes submenu is hidden until parent item is hovered', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Shapes submenu parent should be visible
    await expect(page.getByTestId('timeline-add-shapes-submenu')).toBeVisible()

    // Submenu panel and rectangle button should NOT yet be visible (requires hover)
    await expect(page.getByTestId('timeline-add-shapes-submenu-panel')).toHaveCount(0)
    await expect(page.getByTestId('timeline-add-shape-rectangle')).toHaveCount(0)
  })

  test('Shapes submenu opens on hover and shows shape buttons', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Hover over Shapes parent item
    await page.getByTestId('timeline-add-shapes-submenu').hover()

    // Submenu panel should now be visible
    await expect(page.getByTestId('timeline-add-shapes-submenu-panel')).toBeVisible()

    // All shape buttons should be visible
    await expect(page.getByTestId('timeline-add-shape-rectangle')).toBeVisible()
    await expect(page.getByTestId('timeline-add-shape-circle')).toBeVisible()
    await expect(page.getByTestId('timeline-add-shape-line')).toBeVisible()
    await expect(page.getByTestId('timeline-add-shape-arrow')).toBeVisible()
  })

  test('Audio submenu is hidden until parent item is hovered', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Audio submenu parent should be visible
    await expect(page.getByTestId('timeline-add-audio-submenu')).toBeVisible()

    // Submenu panel should NOT yet be visible (requires hover)
    await expect(page.getByTestId('timeline-add-audio-submenu-panel')).toHaveCount(0)
  })

  test('Audio submenu opens on hover and shows Narration / BGM / SE', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Hover over Audio parent item
    await page.getByTestId('timeline-add-audio-submenu').hover()

    // Audio submenu panel should be visible
    await expect(page.getByTestId('timeline-add-audio-submenu-panel')).toBeVisible()

    // Narration, BGM, SE buttons should be visible
    const panel = page.getByTestId('timeline-add-audio-submenu-panel')
    await expect(panel.locator('button').filter({ hasText: /Narration|ナレーション/ })).toBeVisible()
    await expect(panel.locator('button').filter({ hasText: /BGM/ })).toBeVisible()
    await expect(panel.locator('button').filter({ hasText: /SE/ })).toBeVisible()
  })

  test('clicking Rectangle from Shapes submenu adds a rectangle shape', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Hover over Shapes submenu and click Rectangle
    await page.getByTestId('timeline-add-shapes-submenu').hover()
    await page.getByTestId('timeline-add-shape-rectangle').click()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const addedShape = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips[0]?.shape
    expect(addedShape?.type).toBe('rectangle')
  })

  test('only one submenu is open at a time', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Open the +Add dropdown
    await page.locator('[data-menu-id="add"] button').first().click()

    // Hover Audio submenu
    await page.getByTestId('timeline-add-audio-submenu').hover()
    await expect(page.getByTestId('timeline-add-audio-submenu-panel')).toBeVisible()
    await expect(page.getByTestId('timeline-add-shapes-submenu-panel')).toHaveCount(0)

    // Now hover Shapes submenu - Audio should close, Shapes should open
    await page.getByTestId('timeline-add-shapes-submenu').hover()
    await expect(page.getByTestId('timeline-add-shapes-submenu-panel')).toBeVisible()
    await expect(page.getByTestId('timeline-add-audio-submenu-panel')).toHaveCount(0)
  })
})
