import { expect, test } from '@playwright/test'
import type { Asset } from '../src/api/assets'
import type { AudioTrack } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

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
})
