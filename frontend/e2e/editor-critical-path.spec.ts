import { expect, test } from '@playwright/test'
import type { Locator } from '@playwright/test'
import type { Asset } from '../src/api/assets'
import type { AudioTrack, Clip } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

async function measureCanvasInkHeight(locator: Locator) {
  return locator.evaluate((element: HTMLCanvasElement) => {
    const context = element.getContext('2d')
    if (!context) return 0

    const { width, height } = element
    const pixels = context.getImageData(0, 0, width, height).data
    let top = height
    let bottom = -1

    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const alpha = pixels[(y * width + x) * 4 + 3]
        if (alpha > 0) {
          top = Math.min(top, y)
          bottom = Math.max(bottom, y)
          break
        }
      }
    }

    return bottom >= top ? bottom - top + 1 : 0
  })
}

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

  test('prioritizes current sequence before full asset catalog hydration', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      sequenceDetailDelayMs: 250,
    })
    const deferredAsset: Asset = {
      id: 'asset-image-deferred',
      project_id: mock.projectId,
      name: 'Deferred Asset',
      type: 'image',
      subtype: 'mock',
      storage_key: 'mock/deferred.svg',
      storage_url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"></svg>',
      thumbnail_url: null,
      duration_ms: null,
      width: 32,
      height: 32,
      file_size: 512,
      mime_type: 'image/svg+xml',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const seededClip: Clip = {
      id: 'clip-seeded-priority',
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 3000,
      in_point_ms: 0,
      out_point_ms: null,
      speed: 1,
      freeze_frame_ms: 0,
      transform: {
        x: 0,
        y: 0,
        width: null,
        height: null,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    mock.assetsByProject[mock.projectId].push(deferredAsset)
    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [seededClip]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = [seededClip]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    await page.goto(`/project/${mock.projectId}/sequence/${mock.sequenceId}`)
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()

    await expect.poll(() => mock.calls.sequenceRespondedAt.length).toBeGreaterThan(0)
    await expect.poll(() => mock.calls.assetListRequestedAt.length).toBeGreaterThan(0)
    expect(mock.calls.assetListRequestedAt[0]).toBeGreaterThanOrEqual(mock.calls.sequenceRespondedAt[0])

    await expect(page.getByTestId(`timeline-video-clip-${seededClip.id}`)).toBeVisible()
    await expect(page.getByTestId(`asset-item-${mock.primaryAssetId}`)).toHaveCount(0)
    await expect(page.getByTestId(`asset-item-${deferredAsset.id}`)).toHaveCount(0)

    await page.waitForTimeout(900)
    await expect(page.getByTestId(`asset-item-${mock.primaryAssetId}`)).toBeVisible()
    await expect(page.getByTestId(`asset-item-${deferredAsset.id}`)).toBeVisible()
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

  test('adds a Skitch-style arrow shape through the existing shape flow', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-add-shape-arrow').click()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const addedShape = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips[0]?.shape
    expect(addedShape?.type).toBe('arrow')
    expect(addedShape?.width).toBe(180)
    expect(addedShape?.height).toBe(48)
    expect(addedShape?.strokeColor).toBe('#FF0000')
    await expect(page.getByText(/Arrow|矢印/)).toBeVisible()
  })

  test('returns to the dashboard instead of the landing page from the editor', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('editor-open-exit-confirm').click()
    await expect(page.getByTestId('editor-confirm-exit')).toBeVisible()
    await page.getByTestId('editor-confirm-exit').click()

    await expect(page).toHaveURL(/\/app$/)
    await expect(page.getByText('Seeded Project')).toBeVisible()
  })

  test('does not surface AbortError when stop interrupts a pending audio play()', async ({ page }) => {
    const consoleErrors: string[] = []
    const pageErrors: string[] = []

    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text())
      }
    })
    page.on('pageerror', (error) => {
      pageErrors.push(error.message)
    })

    await page.addInitScript(() => {
      const pendingRejectors = new WeakMap<HTMLMediaElement, (reason?: unknown) => void>()
      const pendingResolvers = new WeakMap<HTMLMediaElement, () => void>()
      const originalPause = HTMLMediaElement.prototype.pause

      HTMLMediaElement.prototype.play = function play() {
        return new Promise<void>((resolve, reject) => {
          pendingResolvers.set(this, () => {
            pendingResolvers.delete(this)
            pendingRejectors.delete(this)
            resolve()
          })
          pendingRejectors.set(this, (reason?: unknown) => {
            pendingResolvers.delete(this)
            pendingRejectors.delete(this)
            reject(reason)
          })
          window.setTimeout(() => {
            pendingResolvers.get(this)?.()
          }, 50)
        })
      }

      HTMLMediaElement.prototype.pause = function pause() {
        const rejectPending = pendingRejectors.get(this)
        if (rejectPending) {
          rejectPending(new DOMException('The play() request was interrupted by a call to pause().', 'AbortError'))
          return
        }
        return originalPause.call(this)
      }
    })

    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-1',
      project_id: mock.projectId,
      name: 'Mock Audio',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/audio.mp4',
      storage_url: '/lp/lp_video_en.mp4',
      thumbnail_url: null,
      duration_ms: 2000,
      width: null,
      height: null,
      file_size: 1024,
      mime_type: 'audio/mp4',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const audioTrack: AudioTrack = {
      id: 'track-audio-1',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-clip-1',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 1200,
          in_point_ms: 0,
          out_point_ms: null,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 1200
    mock.projectDetails[mock.projectId].duration_ms = 1200
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 1200
    mock.sequences[mock.sequenceId].duration_ms = 1200

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('editor-play-toggle').click()
    await page.waitForTimeout(10)
    await page.getByTestId('editor-stop-playback').click()
    await page.waitForTimeout(100)

    expect(pageErrors).toEqual([])
    expect(consoleErrors.filter((entry) => entry.includes('AbortError'))).toEqual([])
  })

  test('keeps cut audio waveform thumbnails aligned after reload when asset duration is unknown', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-cut-1',
      project_id: mock.projectId,
      name: 'Split Sound',
      type: 'audio',
      subtype: 'sound',
      storage_key: 'mock/sound.wav',
      storage_url: '/lp/lp_video_en.mp4',
      thumbnail_url: null,
      duration_ms: null,
      width: null,
      height: null,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const audioTrack: AudioTrack = {
      id: 'track-audio-cut-1',
      name: 'Sound FX',
      type: 'se',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-cut-left',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
        {
          id: 'audio-cut-right',
          asset_id: audioAsset.id,
          start_ms: 3000,
          duration_ms: 3000,
          in_point_ms: 3000,
          out_point_ms: 6000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.waveformsByAsset[audioAsset.id] = {
      peaks: [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6],
      duration_ms: 6000,
      sample_rate: 10,
    }
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 6000
    mock.projectDetails[mock.projectId].duration_ms = 6000
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 6000
    mock.sequences[mock.sequenceId].duration_ms = 6000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const leftCanvas = page.locator('[data-testid="timeline-audio-clip-audio-cut-left"] canvas')
    const rightCanvas = page.locator('[data-testid="timeline-audio-clip-audio-cut-right"] canvas')

    await expect(leftCanvas).toBeVisible()
    await expect(rightCanvas).toBeVisible()

    const leftBeforeReload = await leftCanvas.evaluate((element: HTMLCanvasElement) => element.toDataURL())
    const rightBeforeReload = await rightCanvas.evaluate((element: HTMLCanvasElement) => element.toDataURL())

    expect(leftBeforeReload).not.toEqual(rightBeforeReload)

    await page.reload()
    await page.waitForLoadState('networkidle')
    await page.getByTestId('editor-header').waitFor()

    await expect(leftCanvas).toBeVisible()
    await expect(rightCanvas).toBeVisible()

    const leftAfterReload = await leftCanvas.evaluate((element: HTMLCanvasElement) => element.toDataURL())
    const rightAfterReload = await rightCanvas.evaluate((element: HTMLCanvasElement) => element.toDataURL())

    expect(leftAfterReload).toEqual(leftBeforeReload)
    expect(rightAfterReload).toEqual(rightBeforeReload)
    expect(leftAfterReload).not.toEqual(rightAfterReload)
  })

  test('keeps split audio waveform amplitude scale stable across cut clips', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-cut-normalization',
      project_id: mock.projectId,
      name: 'Split Sound Dynamic',
      type: 'audio',
      subtype: 'sound',
      storage_key: 'mock/sound-dynamic.wav',
      storage_url: '/lp/lp_video_en.mp4',
      thumbnail_url: null,
      duration_ms: 8000,
      width: null,
      height: null,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const audioTrack: AudioTrack = {
      id: 'track-audio-cut-normalization',
      name: 'Sound FX',
      type: 'se',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-cut-soft',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 4000,
          in_point_ms: 0,
          out_point_ms: 4000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
        {
          id: 'audio-cut-loud',
          asset_id: audioAsset.id,
          start_ms: 4000,
          duration_ms: 4000,
          in_point_ms: 4000,
          out_point_ms: 8000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.waveformsByAsset[audioAsset.id] = {
      peaks: [0.08, 0.12, 0.1, 0.14, 0.82, 0.9, 1, 0.88],
      duration_ms: 8000,
      sample_rate: 10,
    }
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 8000
    mock.projectDetails[mock.projectId].duration_ms = 8000
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 8000
    mock.sequences[mock.sequenceId].duration_ms = 8000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const softCanvas = page.locator('[data-testid="timeline-audio-clip-audio-cut-soft"] canvas')
    const loudCanvas = page.locator('[data-testid="timeline-audio-clip-audio-cut-loud"] canvas')

    await expect(softCanvas).toBeVisible()
    await expect(loudCanvas).toBeVisible()

    const softHeight = await measureCanvasInkHeight(softCanvas)
    const loudHeight = await measureCanvasInkHeight(loudCanvas)

    expect(softHeight).toBeGreaterThan(0)
    expect(loudHeight).toBeGreaterThan(softHeight * 2)
  })

  test('uses waveform payload duration when stored asset duration drifts', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-duration-drift',
      project_id: mock.projectId,
      name: 'Narration Drift',
      type: 'audio',
      subtype: 'sound',
      storage_key: 'mock/narration-drift.wav',
      storage_url: '/lp/lp_video_en.mp4',
      thumbnail_url: null,
      duration_ms: 10000,
      width: null,
      height: null,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const audioTrack: AudioTrack = {
      id: 'track-audio-duration-drift',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-drift-left',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
        {
          id: 'audio-drift-right',
          asset_id: audioAsset.id,
          start_ms: 3000,
          duration_ms: 3000,
          in_point_ms: 3000,
          out_point_ms: 6000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.waveformsByAsset[audioAsset.id] = {
      peaks: [0.08, 0.1, 0.12, 0.14, 0.84, 0.92],
      duration_ms: 6000,
      sample_rate: 10,
    }
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 6000
    mock.projectDetails[mock.projectId].duration_ms = 6000
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 6000
    mock.sequences[mock.sequenceId].duration_ms = 6000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const leftCanvas = page.locator('[data-testid="timeline-audio-clip-audio-drift-left"] canvas')
    const rightCanvas = page.locator('[data-testid="timeline-audio-clip-audio-drift-right"] canvas')

    await expect(leftCanvas).toBeVisible()
    await expect(rightCanvas).toBeVisible()

    const leftHeight = await measureCanvasInkHeight(leftCanvas)
    const rightHeight = await measureCanvasInkHeight(rightCanvas)

    expect(leftHeight).toBeGreaterThan(0)
    expect(rightHeight).toBeGreaterThan(leftHeight * 2)
  })

  test('normalizes only the selected audio clips and preserves undoable timeline updates', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAssets: Asset[] = [
      {
        id: 'asset-audio-normalize-a',
        project_id: mock.projectId,
        name: 'Narration A',
        type: 'audio',
        subtype: 'narration',
        storage_key: 'mock/narration-a.wav',
        storage_url: '/lp/lp_video_en.mp4',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 2048,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
      {
        id: 'asset-audio-normalize-b',
        project_id: mock.projectId,
        name: 'Narration B',
        type: 'audio',
        subtype: 'narration',
        storage_key: 'mock/narration-b.wav',
        storage_url: '/lp/lp_video_en.mp4',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 2048,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
      {
        id: 'asset-audio-normalize-c',
        project_id: mock.projectId,
        name: 'Narration C',
        type: 'audio',
        subtype: 'narration',
        storage_key: 'mock/narration-c.wav',
        storage_url: '/lp/lp_video_en.mp4',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 2048,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
    ]
    const audioTrack: AudioTrack = {
      id: 'track-audio-normalize',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-normalize-a',
          asset_id: audioAssets[0].id,
          start_ms: 0,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
        {
          id: 'audio-normalize-b',
          asset_id: audioAssets[1].id,
          start_ms: 2200,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 0.5,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
          volume_keyframes: [
            { time_ms: 0, value: 0.4 },
            { time_ms: 2000, value: 0.5 },
          ],
        },
        {
          id: 'audio-normalize-c',
          asset_id: audioAssets[2].id,
          start_ms: 4400,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 0.7,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.assetsByProject[mock.projectId].push(...audioAssets)
    mock.waveformsByAsset[audioAssets[0].id] = {
      peaks: [0.95, 0.92, 0.9, 0.88],
      duration_ms: 4000,
      sample_rate: 10,
    }
    mock.waveformsByAsset[audioAssets[1].id] = {
      peaks: [0.8, 0.72, 0.7, 0.68],
      duration_ms: 4000,
      sample_rate: 10,
    }
    mock.waveformsByAsset[audioAssets[2].id] = {
      peaks: [0.55, 0.5, 0.48, 0.46],
      duration_ms: 4000,
      sample_rate: 10,
    }
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 6400
    mock.projectDetails[mock.projectId].duration_ms = 6400
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 6400
    mock.sequences[mock.sequenceId].duration_ms = 6400

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clipA = page.getByTestId('timeline-audio-clip-audio-normalize-a')
    const clipB = page.getByTestId('timeline-audio-clip-audio-normalize-b')

    await clipA.click()
    await expect(page.getByTestId('audio-clip-volume-input')).toHaveValue('100')
    await clipB.click({ modifiers: ['Shift'] })
    await clipB.dispatchEvent('contextmenu', { bubbles: true, cancelable: true, button: 2, clientX: 320, clientY: 420 })

    await expect(page.getByTestId('timeline-normalize-audio')).toBeVisible()
    await page.getByTestId('timeline-normalize-audio').click()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)
    await expect(page.getByTestId('audio-clip-volume-input')).toHaveValue('95')

    const updatedClips = mock.calls.sequenceUpdates[0].timelineData.audio_tracks[0].clips
    const updatedA = updatedClips.find((clip) => clip.id === 'audio-normalize-a')
    const updatedB = updatedClips.find((clip) => clip.id === 'audio-normalize-b')
    const updatedC = updatedClips.find((clip) => clip.id === 'audio-normalize-c')

    expect(updatedA?.volume).toBeCloseTo(0.947, 2)
    expect(updatedB?.volume).toBeCloseTo(1, 5)
    expect(updatedB?.volume_keyframes?.[0].value).toBeCloseTo(0.8, 5)
    expect(updatedB?.volume_keyframes?.[1].value).toBeCloseTo(1, 5)
    expect(updatedC?.volume).toBeCloseTo(0.7, 5)
  })

  test('keeps linked audio clips in Shift+click multi-selection for batch actions', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    const audioAssets: Asset[] = [
      {
        id: 'asset-linked-audio-a',
        project_id: mock.projectId,
        name: 'Linked Audio A',
        type: 'audio',
        subtype: 'mock',
        storage_key: 'mock/linked-audio-a.wav',
        storage_url: 'https://example.com/linked-audio-a.wav',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 1024,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
      {
        id: 'asset-linked-audio-b',
        project_id: mock.projectId,
        name: 'Linked Audio B',
        type: 'audio',
        subtype: 'mock',
        storage_key: 'mock/linked-audio-b.wav',
        storage_url: 'https://example.com/linked-audio-b.wav',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 1024,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
      {
        id: 'asset-linked-audio-c',
        project_id: mock.projectId,
        name: 'Linked Audio C',
        type: 'audio',
        subtype: 'mock',
        storage_key: 'mock/linked-audio-c.wav',
        storage_url: 'https://example.com/linked-audio-c.wav',
        thumbnail_url: null,
        duration_ms: 4000,
        width: null,
        height: null,
        file_size: 1024,
        mime_type: 'audio/wav',
        chroma_key_color: null,
        hash: null,
        folder_id: null,
        created_at: '2026-03-07T00:00:00.000Z',
        metadata: null,
      },
    ]

    mock.assetsByProject[mock.projectId].push(...audioAssets)
    mock.waveformsByAsset[audioAssets[0].id] = {
      peaks: [0.95, 0.92, 0.9, 0.88],
      duration_ms: 4000,
      sample_rate: 10,
    }
    mock.waveformsByAsset[audioAssets[1].id] = {
      peaks: [0.8, 0.72, 0.7, 0.68],
      duration_ms: 4000,
      sample_rate: 10,
    }
    mock.waveformsByAsset[audioAssets[2].id] = {
      peaks: [0.55, 0.5, 0.48, 0.46],
      duration_ms: 4000,
      sample_rate: 10,
    }

    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [
      {
        id: 'video-linked-a',
        asset_id: mock.primaryAssetId,
        start_ms: 0,
        duration_ms: 3000,
        in_point_ms: 0,
        out_point_ms: 3000,
        speed: 1,
        freeze_frame_ms: 0,
        group_id: 'group-linked-a',
        transform: {
          x: 0,
          y: 0,
          width: null,
          height: null,
          scale: 1,
          rotation: 0,
        },
        effects: {
          opacity: 1,
        },
      },
      {
        id: 'video-linked-b',
        asset_id: mock.primaryAssetId,
        start_ms: 3200,
        duration_ms: 3000,
        in_point_ms: 0,
        out_point_ms: 3000,
        speed: 1,
        freeze_frame_ms: 0,
        group_id: 'group-linked-b',
        transform: {
          x: 0,
          y: 0,
          width: null,
          height: null,
          scale: 1,
          rotation: 0,
        },
        effects: {
          opacity: 1,
        },
      },
    ]

    const audioTrack: AudioTrack = {
      id: 'track-linked-audio',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-linked-a',
          asset_id: audioAssets[0].id,
          start_ms: 0,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 0.6,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
          group_id: 'group-linked-a',
        },
        {
          id: 'audio-linked-b',
          asset_id: audioAssets[1].id,
          start_ms: 2400,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 0.85,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
          group_id: 'group-linked-b',
          volume_keyframes: [
            { time_ms: 0, value: 0.8 },
            { time_ms: 2000, value: 0.9 },
          ],
        },
        {
          id: 'audio-linked-c',
          asset_id: audioAssets[2].id,
          start_ms: 4800,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 0.7,
          fade_in_ms: 0,
          fade_out_ms: 0,
          speed: 1,
        },
      ],
    }

    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 7000
    mock.projectDetails[mock.projectId].duration_ms = 7000
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = JSON.parse(JSON.stringify(mock.projectDetails[mock.projectId].timeline_data.layers[0].clips))
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(JSON.stringify([audioTrack]))
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 7000
    mock.sequences[mock.sequenceId].duration_ms = 7000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clipA = page.getByTestId('timeline-audio-clip-audio-linked-a')
    const clipB = page.getByTestId('timeline-audio-clip-audio-linked-b')

    await clipA.click()
    await clipB.click({ modifiers: ['Shift'] })
    await clipB.dispatchEvent('contextmenu', { bubbles: true, cancelable: true, button: 2, clientX: 320, clientY: 420 })

    await expect(page.getByTestId('timeline-normalize-audio')).toBeVisible()
    await page.getByTestId('timeline-normalize-audio').click()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedClips = mock.calls.sequenceUpdates[0].timelineData.audio_tracks[0].clips
    const updatedA = updatedClips.find((clip) => clip.id === 'audio-linked-a')
    const updatedB = updatedClips.find((clip) => clip.id === 'audio-linked-b')
    const updatedC = updatedClips.find((clip) => clip.id === 'audio-linked-c')

    expect(updatedA?.volume).toBeCloseTo(0.947, 2)
    expect(updatedB?.volume).toBeGreaterThan(0.85)
    expect(updatedB?.volume_keyframes?.[0].value).toBeGreaterThan(0.8)
    expect(updatedB?.volume_keyframes?.[1].value).toBeGreaterThan(0.9)
    expect(updatedC?.volume).toBeCloseTo(0.7, 5)
  })
})
