/**
 * E2E tests for Issue #179: Duplicate audio clip with volume change
 *
 * Tests the context menu option "Duplicate with volume..." and the
 * resulting dialog + clip creation.
 */
import { test, expect } from '@playwright/test'
import type { AudioTrack } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

const TRACK_ID = 'track-narration-1'
const CLIP_ID = 'clip-narration-1'
const ASSET_ID = 'asset-audio-1'

async function setupEditorWithAudioClip(page: Parameters<typeof bootstrapMockEditorPage>[0]) {
  const mock = await bootstrapMockEditorPage(page)

  // Add an audio asset to the mock
  mock.assetsByProject[mock.projectId].push({
    id: ASSET_ID,
    project_id: mock.projectId,
    name: 'Test Narration',
    type: 'audio',
    subtype: null,
    storage_key: 'mock/narration.mp3',
    storage_url: 'mock://narration.mp3',
    thumbnail_url: null,
    duration_ms: 5000,
    width: null,
    height: null,
    file_size: 1000,
    mime_type: 'audio/mpeg',
    chroma_key_color: null,
    hash: null,
    folder_id: null,
    created_at: '2026-01-01T00:00:00.000Z',
    metadata: null,
  })

  const narrationTrack: AudioTrack = {
    id: TRACK_ID,
    name: 'Narration 1',
    type: 'narration',
    volume: 1,
    muted: false,
    visible: true,
    clips: [
      {
        id: CLIP_ID,
        asset_id: ASSET_ID,
        start_ms: 0,
        duration_ms: 5000,
        in_point_ms: 0,
        out_point_ms: null,
        volume: 1.0,
        fade_in_ms: 0,
        fade_out_ms: 0,
      },
    ],
  }

  mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [narrationTrack]
  mock.projectDetails[mock.projectId].timeline_data.duration_ms = 5000
  mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(
    JSON.stringify([narrationTrack])
  )
  mock.sequences[mock.sequenceId].timeline_data.duration_ms = 5000
  mock.sequences[mock.sequenceId].duration_ms = 5000

  await openSeededEditor(page, mock.projectId, mock.sequenceId)

  return mock
}

test.describe('Duplicate audio clip with volume (#179)', () => {
  test('context menu shows "Duplicate with volume..." for audio clips', async ({ page }) => {
    await setupEditorWithAudioClip(page)

    // Right-click on the audio clip
    const clip = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clip).toBeVisible()
    await clip.click({ button: 'right' })

    // The context menu item should be visible
    const menuItem = page.getByTestId('timeline-duplicate-with-volume')
    await expect(menuItem).toBeVisible()
  })

  test('clicking "Duplicate with volume..." opens the dialog', async ({ page }) => {
    await setupEditorWithAudioClip(page)

    const clip = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clip).toBeVisible()
    await clip.click({ button: 'right' })

    await page.getByTestId('timeline-duplicate-with-volume').click()

    // Dialog should appear
    const input = page.getByTestId('duplicate-with-volume-input')
    await expect(input).toBeVisible()
    // Default volume should be 100% (matching the clip's volume=1.0)
    await expect(input).toHaveValue('100')
  })

  test('confirming duplicate creates a new clip with changed volume', async ({ page }) => {
    const mock = await setupEditorWithAudioClip(page)

    const clip = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clip).toBeVisible()
    await clip.click({ button: 'right' })

    await page.getByTestId('timeline-duplicate-with-volume').click()

    // Change volume to 50%
    const input = page.getByTestId('duplicate-with-volume-input')
    await input.fill('50')

    // Confirm
    await page.getByTestId('duplicate-with-volume-confirm').click()

    // A sequence update should have been sent
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBeGreaterThanOrEqual(1)

    const latestUpdate = mock.calls.sequenceUpdates[mock.calls.sequenceUpdates.length - 1]
    const track = latestUpdate.timelineData.audio_tracks.find((t) => t.id === TRACK_ID)
    expect(track).toBeDefined()
    expect(track!.clips.length).toBe(2)

    // The new clip should have volume 0.5 (50%)
    const newClip = track!.clips.find((c) => c.id !== CLIP_ID)
    expect(newClip).toBeDefined()
    expect(newClip!.volume).toBeCloseTo(0.5, 2)
    // New clip should start right after the original
    expect(newClip!.start_ms).toBe(5000)
  })

  test('cancelling the dialog does not create a clip', async ({ page }) => {
    const mock = await setupEditorWithAudioClip(page)

    const clip = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clip).toBeVisible()
    await clip.click({ button: 'right' })

    await page.getByTestId('timeline-duplicate-with-volume').click()

    // Cancel
    const input = page.getByTestId('duplicate-with-volume-input')
    await expect(input).toBeVisible()
    await page.keyboard.press('Escape')

    // No update should have been sent
    expect(mock.calls.sequenceUpdates.length).toBe(0)
  })
})
