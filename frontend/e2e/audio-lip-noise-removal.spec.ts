import { expect, test } from '@playwright/test'
import type { AudioTrack } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'

/**
 * Regression for #182 (CRITICAL review finding):
 * The "lip noise removal" toggle in the audio clip inspector must actually
 * persist `lip_noise_removal` onto the clip so the render pipeline can apply
 * the FFmpeg adeclick filter. Pre-fix, Editor.handleUpdateAudioClip dropped
 * the field, so toggling did nothing and the value was lost on reload.
 */

const TRACK_ID = 'track-narration-1'
const CLIP_ID = 'clip-narration-1'

async function setupEditorWithAudioClip(page: Parameters<typeof bootstrapMockEditorPage>[0]) {
  const mock = await bootstrapMockEditorPage(page)

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
        asset_id: mock.primaryAssetId,
        start_ms: 0,
        duration_ms: 5000,
        in_point_ms: 0,
        out_point_ms: null,
        volume: 1,
        fade_in_ms: 0,
        fade_out_ms: 0,
      },
    ],
  }

  // Seed the same track into both the project detail and the sequence so the
  // editor loads it regardless of which source it reads from.
  mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [narrationTrack]
  mock.projectDetails[mock.projectId].timeline_data.duration_ms = 5000
  mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(
    JSON.stringify([narrationTrack]),
  )
  mock.sequences[mock.sequenceId].timeline_data.duration_ms = 5000
  mock.sequences[mock.sequenceId].duration_ms = 5000

  await openSeededEditor(page, mock.projectId, mock.sequenceId)

  return mock
}

test.describe('Audio lip noise removal toggle (issue #182)', () => {
  test('toggle persists lip_noise_removal to the saved sequence and survives reload', async ({
    page,
  }) => {
    const mock = await setupEditorWithAudioClip(page)

    // Select the seeded audio clip to open the audio clip inspector.
    const clip = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clip).toBeVisible()
    await clip.click()

    // The toggle starts off (default false).
    const toggle = page.getByTestId('lip-noise-removal-toggle')
    await expect(toggle).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-checked', 'false')

    // Enable lip noise removal.
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')

    // The change is committed to the server with lip_noise_removal=true.
    await expect
      .poll(() => mock.calls.sequenceUpdates.length)
      .toBeGreaterThanOrEqual(1)
    const lastUpdate = mock.calls.sequenceUpdates[mock.calls.sequenceUpdates.length - 1]
    const savedClip = lastUpdate.timelineData.audio_tracks[0].clips[0]
    expect(savedClip.lip_noise_removal).toBe(true)

    // Reload: the mock server returns the persisted sequence, so the toggle
    // must come back enabled (proves the value round-trips through save+load).
    await page.goto(`/project/${mock.projectId}/sequence/${mock.sequenceId}`)
    await page.waitForLoadState('networkidle')
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()

    const clipAfterReload = page.getByTestId(`timeline-audio-clip-${CLIP_ID}`)
    await expect(clipAfterReload).toBeVisible()
    await clipAfterReload.click()

    const toggleAfterReload = page.getByTestId('lip-noise-removal-toggle')
    await expect(toggleAfterReload).toBeVisible()
    await expect(toggleAfterReload).toHaveAttribute('aria-checked', 'true')
  })
})
