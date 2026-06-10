import { test, expect } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

/**
 * Video Drag & Drop tests (Issue #276).
 *
 * These tests use the mockServer pattern (bootstrapMockEditorPage + openSeededEditor)
 * so they run entirely offline without authentication or a real backend.
 */

test.describe('Video Drag & Drop — asset library UI', () => {
  test('asset library renders at least one asset item', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // The seeded image asset should appear in the asset library
    const assetItem = page.getByTestId(`asset-item-${mock.primaryAssetId}`)
    await expect(assetItem).toBeVisible({ timeout: 5000 })
  })

  test('asset item has draggable attribute', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const assetItem = page.getByTestId(`asset-item-${mock.primaryAssetId}`)
    await expect(assetItem).toBeVisible({ timeout: 5000 })

    const isDraggable = await assetItem.getAttribute('draggable')
    expect(isDraggable).toBe('true')
  })

  test('video layer row exists in timeline', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // The seeded timeline has layer-1
    const layerRow = page.getByTestId('video-layer-layer-1')
    await expect(layerRow).toBeVisible({ timeout: 5000 })
  })
})

test.describe('Video Drag & Drop — drop to layer', () => {
  test('dragging asset to video layer adds a clip', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // Drag the seeded image asset onto layer-1
    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 180,
    })

    // The mock server should record a sequence update with a new clip
    await expect.poll(() => mock.calls.sequenceUpdates.length, { timeout: 5000 }).toBeGreaterThanOrEqual(1)

    const update = mock.calls.sequenceUpdates[0]
    expect(update.timelineData.layers[0].clips.length).toBeGreaterThanOrEqual(1)
  })

  test('dropped clip is rendered in the timeline', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 180,
    })

    // Wait for the sequence update to reach the mock server
    await expect.poll(() => mock.calls.sequenceUpdates.length, { timeout: 5000 }).toBeGreaterThanOrEqual(1)

    // At least one video clip testid should be present
    const clips = page.locator('[data-testid^="timeline-video-clip-"]')
    await expect(clips.first()).toBeVisible({ timeout: 5000 })
  })

  test('dropped clip can be selected by click', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })

    await expect.poll(() => mock.calls.sequenceUpdates.length, { timeout: 5000 }).toBeGreaterThanOrEqual(1)

    const clip = page.locator('[data-testid^="timeline-video-clip-"]').first()
    await clip.click()

    // Selected clip should have data-selected attribute set to 'true'
    await expect(clip).toHaveAttribute('data-selected', 'true', { timeout: 3000 })
  })
})
