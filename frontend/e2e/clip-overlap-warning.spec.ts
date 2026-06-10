/**
 * Browser E2E tests for Issue #177: clip overlap warning icon
 *
 * Verifies that the warning icon (data-testid="video-clip-overlap-warning-*" /
 * "audio-clip-overlap-warning-*") rendered by VideoLayers / AudioTracks is
 * actually visible in the DOM when clips overlap — and absent when they don't.
 *
 * Unit tests for the underlying detectOverlaps utility live in
 * src/utils/clipOverlap.test.ts (vitest).
 */
import { test, expect } from '@playwright/test'
import type { AudioTrack, Clip } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

const TRACK_ID = 'track-narration-1'
const ASSET_ID = 'asset-audio-1'

// Minimal shape clip — no asset needed, renders standalone in the timeline
function buildShapeClip(id: string, startMs: number, durationMs: number): Clip {
  return {
    id,
    asset_id: null,
    shape: {
      type: 'rectangle',
      width: 200,
      height: 150,
      fillColor: '#ef4444',
      strokeColor: '#ffffff',
      strokeWidth: 2,
      filled: true,
    },
    start_ms: startMs,
    duration_ms: durationMs,
    in_point_ms: 0,
    out_point_ms: null,
    transform: { x: 0, y: 0, width: null, height: null, scale: 1, rotation: 0 },
    effects: { opacity: 1 },
  }
}

function buildAudioClip(id: string, startMs: number, durationMs: number) {
  return {
    id,
    asset_id: ASSET_ID,
    start_ms: startMs,
    duration_ms: durationMs,
    in_point_ms: 0,
    out_point_ms: null,
    volume: 1.0,
    fade_in_ms: 0,
    fade_out_ms: 0,
  }
}

async function seedVideoClips(
  page: Parameters<typeof bootstrapMockEditorPage>[0],
  clips: Clip[]
) {
  const mock = await bootstrapMockEditorPage(page)
  const sequence = mock.sequences[mock.sequenceId]
  sequence.timeline_data.duration_ms = 10000
  sequence.timeline_data.layers[0].clips = clips
  mock.projectDetails[mock.projectId].timeline_data = JSON.parse(
    JSON.stringify(sequence.timeline_data)
  )
  await openSeededEditor(page, mock.projectId, mock.sequenceId)
  return mock
}

async function seedAudioClips(
  page: Parameters<typeof bootstrapMockEditorPage>[0],
  clips: ReturnType<typeof buildAudioClip>[]
) {
  const mock = await bootstrapMockEditorPage(page)

  mock.assetsByProject[mock.projectId].push({
    id: ASSET_ID,
    project_id: mock.projectId,
    name: 'Test Narration',
    type: 'audio',
    subtype: null,
    storage_key: 'mock/narration.mp3',
    storage_url: 'mock://narration.mp3',
    thumbnail_url: null,
    duration_ms: 60000,
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
    clips,
  }

  const sequence = mock.sequences[mock.sequenceId]
  sequence.timeline_data.audio_tracks = [narrationTrack]
  sequence.timeline_data.duration_ms = 10000
  mock.projectDetails[mock.projectId].timeline_data = JSON.parse(
    JSON.stringify(sequence.timeline_data)
  )

  await openSeededEditor(page, mock.projectId, mock.sequenceId)
  return mock
}

test.describe('Clip overlap warning icon (#177)', () => {
  test('overlapping video clips show warning icons on both clips', async ({ page }) => {
    // clip-a: 0..3000, clip-b: 2000..5000 → overlap 2000..3000
    await seedVideoClips(page, [
      buildShapeClip('clip-a', 0, 3000),
      buildShapeClip('clip-b', 2000, 3000),
    ])

    await expect(page.getByTestId('timeline-video-clip-clip-a')).toBeVisible()
    await expect(page.getByTestId('timeline-video-clip-clip-b')).toBeVisible()

    await expect(page.getByTestId('video-clip-overlap-warning-clip-a')).toBeVisible()
    await expect(page.getByTestId('video-clip-overlap-warning-clip-b')).toBeVisible()
  })

  test('adjacent (non-overlapping) video clips show no warning icon', async ({ page }) => {
    // clip-a: 0..3000, clip-b: 3000..6000 → touching boundary, NO overlap
    await seedVideoClips(page, [
      buildShapeClip('clip-a', 0, 3000),
      buildShapeClip('clip-b', 3000, 3000),
    ])

    await expect(page.getByTestId('timeline-video-clip-clip-a')).toBeVisible()
    await expect(page.getByTestId('timeline-video-clip-clip-b')).toBeVisible()

    await expect(page.locator('[data-testid^="video-clip-overlap-warning-"]')).toHaveCount(0)
  })

  test('overlapping audio clips show warning icons on both clips', async ({ page }) => {
    // audio-a: 0..5000, audio-b: 3000..8000 → overlap 3000..5000
    await seedAudioClips(page, [
      buildAudioClip('audio-a', 0, 5000),
      buildAudioClip('audio-b', 3000, 5000),
    ])

    await expect(page.getByTestId('timeline-audio-clip-audio-a')).toBeVisible()
    await expect(page.getByTestId('timeline-audio-clip-audio-b')).toBeVisible()

    await expect(page.getByTestId('audio-clip-overlap-warning-audio-a')).toBeVisible()
    await expect(page.getByTestId('audio-clip-overlap-warning-audio-b')).toBeVisible()
  })

  test('dropping an asset onto an occupied range shows warning icons (drop-time alert)', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    // No icons initially
    await expect(page.locator('[data-testid^="video-clip-overlap-warning-"]')).toHaveCount(0)

    // Drop the same image asset twice at nearly the same position → clips overlap
    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 240,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)

    // Both clips should now carry the overlap warning icon
    await expect(page.locator('[data-testid^="video-clip-overlap-warning-"]')).toHaveCount(2)
  })
})
