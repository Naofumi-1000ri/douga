import { expect, test } from '@playwright/test'
import type { Locator } from '@playwright/test'
import type { Asset } from '../src/api/assets'
import { getArrowHeadLength, getArrowShapePath } from '../src/components/editor/shapeGeometry'
import type { AudioTrack, Clip, TimelineData } from '../src/store/projectStore'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { dragAssetToAudioTrack, dragAssetToVideoLayer, openSeededEditor } from './helpers/editorPage'

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

  test('drops an audio asset onto an empty track at the hovered snapped time', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-empty-track-drop',
      project_id: mock.projectId,
      name: 'Voice Line',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/voice-line.wav',
      storage_url: 'https://example.com/voice-line.wav',
      thumbnail_url: null,
      duration_ms: 2000,
      width: null,
      height: null,
      file_size: 4096,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }

    const sourceTrack: AudioTrack = {
      id: 'track-source',
      name: 'Narration 1',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-source-clip',
          asset_id: audioAsset.id,
          start_ms: 5000,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }

    const emptyTrack: AudioTrack = {
      id: 'track-empty',
      name: 'Narration 2',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [sourceTrack, emptyTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 12000
    mock.projectDetails[mock.projectId].duration_ms = 12000
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(JSON.stringify([sourceTrack, emptyTrack]))
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 12000
    mock.sequences[mock.sequenceId].duration_ms = 12000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await expect(page.getByTestId(`asset-item-${audioAsset.id}`)).toBeVisible()
    await dragAssetToAudioTrack(page, {
      assetId: audioAsset.id,
      trackId: 'track-empty',
      offsetX: 67,
    })

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedEmptyTrack = mock.calls.sequenceUpdates[0].timelineData.audio_tracks.find(
      (track) => track.id === 'track-empty'
    )

    expect(updatedEmptyTrack?.clips).toHaveLength(1)
    expect(updatedEmptyTrack?.clips[0].start_ms).toBe(7000)
    expect(updatedEmptyTrack?.clips[0].duration_ms).toBe(2000)
  })

  test('moves an existing audio clip onto an empty track without losing it', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-cross-track',
      project_id: mock.projectId,
      name: 'Cross Track Voice',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/cross-track.wav',
      storage_url: 'https://example.com/cross-track.wav',
      thumbnail_url: null,
      duration_ms: 2000,
      width: null,
      height: null,
      file_size: 4096,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }

    const sourceTrack: AudioTrack = {
      id: 'track-cross-source',
      name: 'Narration 1',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'audio-cross-source-clip',
          asset_id: audioAsset.id,
          start_ms: 5000,
          duration_ms: 2000,
          in_point_ms: 0,
          out_point_ms: 2000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }

    const emptyTrack: AudioTrack = {
      id: 'track-cross-empty',
      name: 'Narration 2',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [],
    }

    mock.assetsByProject[mock.projectId].push(audioAsset)
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [sourceTrack, emptyTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 12000
    mock.projectDetails[mock.projectId].duration_ms = 12000
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = JSON.parse(JSON.stringify([sourceTrack, emptyTrack]))
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 12000
    mock.sequences[mock.sequenceId].duration_ms = 12000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const clip = page.getByTestId('timeline-audio-clip-audio-cross-source-clip')
    const clipBox = await clip.boundingBox()
    const targetTrack = page.getByTestId('timeline-audio-track-row-track-cross-empty')
    const targetTrackBox = await targetTrack.boundingBox()

    expect(clipBox).toBeTruthy()
    expect(targetTrackBox).toBeTruthy()

    await page.mouse.move(clipBox!.x + clipBox!.width / 2, clipBox!.y + clipBox!.height / 2)
    await page.mouse.down()
    await page.mouse.move(
      clipBox!.x + clipBox!.width / 2 + 30,
      targetTrackBox!.y + targetTrackBox!.height / 2,
      { steps: 10 }
    )
    await page.mouse.up()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedSourceTrack = mock.calls.sequenceUpdates[0].timelineData.audio_tracks.find(
      (track) => track.id === 'track-cross-source'
    )
    const updatedEmptyTrack = mock.calls.sequenceUpdates[0].timelineData.audio_tracks.find(
      (track) => track.id === 'track-cross-empty'
    )

    expect(updatedSourceTrack?.clips).toHaveLength(0)
    expect(updatedEmptyTrack?.clips).toHaveLength(1)
    expect(updatedEmptyTrack?.clips[0].id).toBe('audio-cross-source-clip')
    expect(updatedEmptyTrack?.clips[0].start_ms).toBeGreaterThan(5000)
  })

  test('recovers sequence save after a stale lock rejects the first save', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    let rejectedSaveCount = 0
    let lockRefreshCount = 0

    await page.route(`**/api/projects/${mock.projectId}/sequences/${mock.sequenceId}`, async (route) => {
      const request = route.request()
      if (request.method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mock.sequences[mock.sequenceId]),
        })
        return
      }

      if (request.method() !== 'PUT') {
        await route.abort()
        return
      }

      if (rejectedSaveCount === 0) {
        rejectedSaveCount += 1
        await route.fulfill({
          status: 403,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'Sequence is not locked by you. Acquire a lock before saving.',
          }),
        })
        return
      }

      const body = request.postDataJSON() as { timeline_data: TimelineData; version: number }
      const sequence = mock.sequences[mock.sequenceId]
      sequence.timeline_data = JSON.parse(JSON.stringify(body.timeline_data))
      sequence.duration_ms = body.timeline_data.duration_ms
      sequence.version += 1
      sequence.updated_at = new Date().toISOString()

      mock.calls.sequenceUpdates.push({
        projectId: mock.projectId,
        sequenceId: mock.sequenceId,
        timelineData: JSON.parse(JSON.stringify(body.timeline_data)),
        version: sequence.version,
      })

      mock.projectDetails[mock.projectId].timeline_data = JSON.parse(JSON.stringify(sequence.timeline_data))
      mock.projectDetails[mock.projectId].duration_ms = sequence.duration_ms
      mock.projectDetails[mock.projectId].version = sequence.version
      mock.projectDetails[mock.projectId].updated_at = sequence.updated_at

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(sequence),
      })
    })

    await page.route(`**/api/projects/${mock.projectId}/sequences/${mock.sequenceId}/lock`, async (route) => {
      lockRefreshCount += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          locked: true,
          locked_by: 'dev-user-123',
          lock_holder_name: 'Dev User',
          locked_at: new Date().toISOString(),
          edit_token: `mock-edit-token-${lockRefreshCount}`,
        }),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 220,
    })

    await expect.poll(() => rejectedSaveCount).toBe(1)
    await expect.poll(() => lockRefreshCount).toBeGreaterThan(1)
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)
    await expect(page.locator('[data-testid^="timeline-video-clip-"]')).toHaveCount(1)
    await expect(page.getByText('最新のタイムライン変更を保存できていません。')).toHaveCount(0)
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

  test('streams AI chat responses inside the editor panel without breaking the workflow', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAIChatOpen: true,
      },
    })

    const textClip: Clip = {
      id: 'clip-seeded-text-ai',
      asset_id: null,
      text_content: 'Seeded telop',
      text_style: {
        fontFamily: 'Noto Sans JP',
        fontSize: 42,
        fontWeight: 'bold',
        fontStyle: 'normal',
        color: '#ffffff',
        backgroundColor: '#000000',
        backgroundOpacity: 0.4,
        textAlign: 'center',
        verticalAlign: 'middle',
        lineHeight: 1.4,
        letterSpacing: 0,
        strokeColor: '#000000',
        strokeWidth: 2,
      },
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

    mock.projectDetails[mock.projectId].timeline_data.layers = [
      {
        id: 'layer-text-ai',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [
      {
        id: 'layer-text-ai',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const chatRequests: Array<{ message: string; history: Array<{ role: string; content: string }>; provider?: string }> = []

    await page.route(`**/api/ai/project/${mock.projectId}/chat/stream`, async (route) => {
      chatRequests.push(route.request().postDataJSON() as { message: string; history: Array<{ role: string; content: string }>; provider?: string })
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'cache-control': 'no-cache',
        },
        body: [
          'event: chunk',
          'data: {"text":"既存テキストは「Seeded telop」です。"}',
          '',
          'event: done',
          'data: {}',
          '',
        ].join('\n'),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)
    await expect(page.getByTestId('ai-chat-panel')).toBeVisible()
    await expect(page.getByTestId('ai-chat-input')).toBeVisible()

    await page.getByTestId('ai-chat-input').fill('今表示されているテキストを確認して')
    await page.getByTestId('ai-chat-input').press('Enter')

    await expect.poll(() => chatRequests.length).toBe(1)
    expect(chatRequests[0]?.message).toBe('今表示されているテキストを確認して')
    expect(chatRequests[0]?.history).toEqual([])

    await expect(page.getByText('今表示されているテキストを確認して')).toBeVisible()
    await expect(page.getByText('既存テキストは「Seeded telop」です。')).toBeVisible()
  })

  test('syncs timeline preview and properties within 5s after AI updates an existing telop', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page, {
      layout: {
        isAIChatOpen: true,
        isPropertyPanelOpen: true,
      },
    })

    const textClip: Clip = {
      id: 'clip-ai-refresh-text',
      asset_id: null,
      text_content: 'Seeded telop',
      text_style: {
        fontFamily: 'Noto Sans JP',
        fontSize: 42,
        fontWeight: 'bold',
        fontStyle: 'normal',
        color: '#ffffff',
        backgroundColor: '#000000',
        backgroundOpacity: 0.4,
        textAlign: 'center',
        verticalAlign: 'middle',
        lineHeight: 1.4,
        letterSpacing: 0,
        strokeColor: '#000000',
        strokeWidth: 2,
      },
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

    mock.projectDetails[mock.projectId].timeline_data.layers = [
      {
        id: 'layer-text-ai-initial',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [
      {
        id: 'layer-text-ai-initial',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const updatedText = 'AI更新テロップ'

    await page.route(`**/api/ai/project/${mock.projectId}/chat/stream`, async (route) => {
      mock.sequences[mock.sequenceId] = {
        ...mock.sequences[mock.sequenceId],
        version: 2,
        duration_ms: 3000,
        updated_at: '2026-03-07T00:00:05.000Z',
        timeline_data: {
          ...mock.sequences[mock.sequenceId].timeline_data,
          duration_ms: 3000,
          layers: [
            {
              id: 'layer-text-ai-initial',
              name: 'Telops',
              type: 'text',
              order: 0,
              visible: true,
              locked: false,
              clips: [
                {
                  ...textClip,
                  text_content: updatedText,
                },
              ],
            },
          ],
        },
      }

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'cache-control': 'no-cache',
        },
        body: [
          'event: chunk',
          `data: ${JSON.stringify({ text: 'テロップを更新しました。' })}`,
          '',
          'event: actions',
          `data: ${JSON.stringify([{ type: 'clip.text', description: 'Updated telop text', applied: true }])}`,
          '',
          'event: done',
          'data: {}',
          '',
        ].join('\n'),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)
    await expect(page.getByTestId('ai-chat-panel')).toBeVisible()
    await expect(page.getByTestId('right-panel')).toBeVisible()

    const timelineTextClip = page.getByTestId(`timeline-video-clip-${textClip.id}`)
    await timelineTextClip.click()

    const propertyTextInput = page.getByTestId('right-panel').locator('textarea').first()
    const previewTextClip = page.getByTestId(`preview-text-clip-${textClip.id}`)
    const sequenceRequestCountBeforeAi = mock.calls.sequenceRequestedAt.length

    await expect(propertyTextInput).toHaveValue('Seeded telop')
    await expect(previewTextClip).toContainText('Seeded telop')
    await expect(timelineTextClip).toContainText('Seeded telop')

    await page.getByTestId('ai-chat-input').fill('このテロップを新しい文言に変えて')
    await page.getByTestId('ai-chat-input').press('Enter')

    await expect(page.getByText('このテロップを新しい文言に変えて')).toBeVisible()
    await expect(page.getByText('テロップを更新しました。')).toBeVisible()
    await expect.poll(() => mock.calls.sequenceRequestedAt.length, { timeout: 5000 }).toBeGreaterThan(sequenceRequestCountBeforeAi)

    await expect(page.getByTestId('video-layer-layer-text-ai-initial')).toBeVisible({ timeout: 5000 })
    await expect(timelineTextClip).toContainText(updatedText, { timeout: 5000 })
    await expect(previewTextClip).toContainText(updatedText, { timeout: 5000 })
    await expect(propertyTextInput).toHaveValue(updatedText, { timeout: 5000 })
  })

  test('renders preview safely when a text clip only has partial text_style data', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const pageErrors: string[] = []

    page.on('pageerror', (error) => {
      pageErrors.push(error.message)
    })

    const textClip: Clip = {
      id: 'clip-partial-style',
      asset_id: null,
      text_content: '長いテロップ前半',
      text_style: {
        fontSize: 44,
        color: '#ffffff',
        fontWeight: 'bold',
      } as Clip['text_style'],
      start_ms: 0,
      duration_ms: 4000,
      in_point_ms: 0,
      out_point_ms: null,
      speed: 1,
      freeze_frame_ms: 0,
      transform: {
        x: 0,
        y: 320,
        width: null,
        height: null,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    mock.projectDetails[mock.projectId].timeline_data.layers = [
      {
        id: 'layer-text-partial',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 4000
    mock.projectDetails[mock.projectId].duration_ms = 4000
    mock.sequences[mock.sequenceId].timeline_data.layers = [
      {
        id: 'layer-text-partial',
        name: 'Telops',
        type: 'text',
        order: 0,
        visible: true,
        locked: false,
        clips: [textClip],
      },
    ]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 4000
    mock.sequences[mock.sequenceId].duration_ms = 4000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const previewTextClip = page.getByTestId('preview-text-clip-clip-partial-style')
    await expect(previewTextClip).toBeVisible()
    await expect(previewTextClip.getByText('長いテロップ前半')).toBeVisible()
    await page.waitForTimeout(200)
    expect(pageErrors).toEqual([])
  })

  test('auto-generate telop uses an explicitly selected audio track source', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-telop-source',
      project_id: mock.projectId,
      name: 'Mock Narration Source',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/telop-source.wav',
      storage_url: 'https://example.invalid/mock-telop-source.wav',
      thumbnail_url: null,
      duration_ms: 3000,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const imageOnlyClip: Clip = {
      id: 'clip-seeded-image-layer',
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
    const audioTrack: AudioTrack = {
      id: 'track-narration',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'clip-seeded-audio-track',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }
    const imageLayer = {
      ...mock.projectDetails[mock.projectId].timeline_data.layers[0],
      clips: [imageOnlyClip],
    }

    mock.assetsByProject[mock.projectId] = [...mock.assetsByProject[mock.projectId], audioAsset]
    mock.projectDetails[mock.projectId].timeline_data.layers = [imageLayer]
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [imageLayer]
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const telopRequests: Array<{ source_type: string; source_id: string }> = []
    const dialogMessages: string[] = []

    page.on('dialog', async (dialog) => {
      dialogMessages.push(dialog.message())
      await dialog.accept()
    })

    await page.route(`**/api/ai-video/projects/${mock.projectId}/generate-telop`, async (route) => {
      telopRequests.push(route.request().postDataJSON() as { source_type: string; source_id: string })
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          project_id: mock.projectId,
          skill: 'generate-telop',
          success: true,
          message: 'Added 3 telop clip(s) on a new layer.',
          changes: { telops_added: 3, layer_id: 'layer-telop-generated' },
          duration_ms: 120,
        }),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)
    await expect(page.getByTestId(`timeline-video-clip-${imageOnlyClip.id}`)).toBeVisible()
    await expect(page.getByTestId('timeline-audio-track-header-track-narration')).toBeVisible()

    await page.getByTestId('video-layer-layer-1').click()

    await page.locator('[data-menu-id="add"] button').first().click()
    await expect(page.getByText('Selected layer has no audio/video clips for telop generation')).toBeVisible()
    await expect(page.getByTestId('timeline-generate-telop')).toBeDisabled()

    await page.getByTestId('timeline-audio-track-header-track-narration').click()
    await page.locator('[data-menu-id="add"] button').first().click()
    await expect(page.getByText('Telop source: audio track Narration')).toBeVisible()
    await expect(page.getByTestId('timeline-generate-telop')).toBeEnabled()
    await page.getByTestId('timeline-generate-telop').click()

    await expect.poll(() => telopRequests.length).toBe(1)
    expect(telopRequests[0]).toEqual({ source_type: 'audio_track', source_id: 'track-narration' })
    await expect.poll(() => dialogMessages.at(-1)).toBe('Added 3 telop clip(s) on a new layer.')
  })

  test('auto-generate telop keeps progress feedback visible until the request finishes', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-telop-progress-source',
      project_id: mock.projectId,
      name: 'Mock Narration Source',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/telop-progress-source.wav',
      storage_url: 'https://example.invalid/mock-telop-progress-source.wav',
      thumbnail_url: null,
      duration_ms: 3000,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const imageOnlyClip: Clip = {
      id: 'clip-seeded-image-layer-progress',
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
    const audioTrack: AudioTrack = {
      id: 'track-narration-progress',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'clip-seeded-audio-track-progress',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }
    const imageLayer = {
      ...mock.projectDetails[mock.projectId].timeline_data.layers[0],
      clips: [imageOnlyClip],
    }

    mock.assetsByProject[mock.projectId] = [...mock.assetsByProject[mock.projectId], audioAsset]
    mock.projectDetails[mock.projectId].timeline_data.layers = [imageLayer]
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [imageLayer]
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const telopRequests: Array<{ source_type: string; source_id: string }> = []
    const dialogMessages: string[] = []
    let releaseTelopResponse: (() => void) | null = null
    const telopResponseReleased = new Promise<void>((resolve) => {
      releaseTelopResponse = resolve
    })

    page.on('dialog', async (dialog) => {
      dialogMessages.push(dialog.message())
      await dialog.accept()
    })

    await page.route(`**/api/ai-video/projects/${mock.projectId}/generate-telop`, async (route) => {
      telopRequests.push(route.request().postDataJSON() as { source_type: string; source_id: string })
      await telopResponseReleased
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          project_id: mock.projectId,
          skill: 'generate-telop',
          success: true,
          message: 'Added 2 telop clip(s) on a new layer.',
          changes: { telops_added: 2, layer_id: 'layer-telop-progress-generated' },
          duration_ms: 120,
        }),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('timeline-audio-track-header-track-narration-progress').click()
    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-generate-telop').click()

    await expect.poll(() => telopRequests.length).toBe(1)
    await expect(page.getByTestId('timeline-generate-telop')).toHaveCount(0)
    await expect(page.getByTestId('timeline-telop-status')).toBeVisible()
    await expect(page.getByTestId('timeline-telop-status')).toContainText('Generating telop from audio track Narration')

    releaseTelopResponse?.()

    await expect.poll(() => dialogMessages.at(-1)).toBe('Added 2 telop clip(s) on a new layer.')
    await expect(page.getByTestId('timeline-telop-status')).toContainText('Added 2 telop clip(s) on a new layer.')
  })

  test('auto-generate telop leaves an understandable error state after a failed request', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-telop-error-source',
      project_id: mock.projectId,
      name: 'Mock Narration Source',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/telop-error-source.wav',
      storage_url: 'https://example.invalid/mock-telop-error-source.wav',
      thumbnail_url: null,
      duration_ms: 3000,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const imageOnlyClip: Clip = {
      id: 'clip-seeded-image-layer-error',
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
    const audioTrack: AudioTrack = {
      id: 'track-narration-error',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'clip-seeded-audio-track-error',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }
    const imageLayer = {
      ...mock.projectDetails[mock.projectId].timeline_data.layers[0],
      clips: [imageOnlyClip],
    }

    mock.assetsByProject[mock.projectId] = [...mock.assetsByProject[mock.projectId], audioAsset]
    mock.projectDetails[mock.projectId].timeline_data.layers = [imageLayer]
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [imageLayer]
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const dialogMessages: string[] = []
    let releaseTelopResponse: (() => void) | null = null
    const telopResponseReleased = new Promise<void>((resolve) => {
      releaseTelopResponse = resolve
    })

    page.on('dialog', async (dialog) => {
      dialogMessages.push(dialog.message())
      await dialog.accept()
    })

    await page.route(`**/api/ai-video/projects/${mock.projectId}/generate-telop`, async (route) => {
      await telopResponseReleased
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'Mock telop generation failed.',
        }),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('timeline-audio-track-header-track-narration-error').click()
    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-generate-telop').click()

    await expect(page.getByTestId('timeline-telop-status')).toContainText('Generating telop from audio track Narration')

    releaseTelopResponse?.()

    await expect.poll(() => dialogMessages.at(-1)).toBe('Mock telop generation failed.')
    await expect(page.getByTestId('timeline-telop-status')).toContainText('Mock telop generation failed.')
    await page.getByTestId('timeline-telop-status-dismiss').click()
    await expect(page.getByTestId('timeline-telop-status')).toHaveCount(0)
  })

  test('auto-generate telop keeps generated layer on top after sequence refresh', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const audioAsset: Asset = {
      id: 'asset-audio-preview-depth-source',
      project_id: mock.projectId,
      name: 'Mock Narration Source',
      type: 'audio',
      subtype: 'mock',
      storage_key: 'mock/telop-source.wav',
      storage_url: 'https://example.invalid/mock-telop-source.wav',
      thumbnail_url: null,
      duration_ms: 3000,
      file_size: 2048,
      mime_type: 'audio/wav',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    const imageOnlyClip: Clip = {
      id: 'clip-seeded-image-layer',
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
    const generatedTelopClip: Clip = {
      id: 'clip-generated-telop',
      asset_id: null,
      text_content: 'Generated Telop',
      text_style: {
        fontFamily: 'Noto Sans JP',
        fontSize: 48,
        fontWeight: 'bold',
        fontStyle: 'normal',
        color: '#ffffff',
        backgroundColor: '#000000',
        backgroundOpacity: 0.6,
        textAlign: 'center',
        verticalAlign: 'middle',
        lineHeight: 1.4,
        letterSpacing: 0,
        strokeColor: '#000000',
        strokeWidth: 2,
      },
      start_ms: 0,
      duration_ms: 3000,
      in_point_ms: 0,
      out_point_ms: null,
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
    const audioTrack: AudioTrack = {
      id: 'track-narration',
      name: 'Narration',
      type: 'narration',
      volume: 1,
      muted: false,
      visible: true,
      clips: [
        {
          id: 'clip-seeded-audio-track',
          asset_id: audioAsset.id,
          start_ms: 0,
          duration_ms: 3000,
          in_point_ms: 0,
          out_point_ms: 3000,
          volume: 1,
          fade_in_ms: 0,
          fade_out_ms: 0,
        },
      ],
    }
    const imageLayer = {
      ...mock.projectDetails[mock.projectId].timeline_data.layers[0],
      order: 0,
      clips: [imageOnlyClip],
    }
    const generatedLayer = {
      id: 'layer-generated-telop',
      name: 'テロップ（自動生成）',
      type: 'text' as const,
      order: 1,
      visible: true,
      locked: false,
      clips: [generatedTelopClip],
    }

    mock.assetsByProject[mock.projectId] = [...mock.assetsByProject[mock.projectId], audioAsset]
    mock.projectDetails[mock.projectId].timeline_data.layers = [imageLayer]
    mock.projectDetails[mock.projectId].timeline_data.audio_tracks = [audioTrack]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 3000
    mock.projectDetails[mock.projectId].duration_ms = 3000
    mock.sequences[mock.sequenceId].timeline_data.layers = [imageLayer]
    mock.sequences[mock.sequenceId].timeline_data.audio_tracks = [audioTrack]
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 3000
    mock.sequences[mock.sequenceId].duration_ms = 3000

    const dialogMessages: string[] = []

    page.on('dialog', async (dialog) => {
      dialogMessages.push(dialog.message())
      await dialog.accept()
    })

    await page.route(`**/api/ai-video/projects/${mock.projectId}/generate-telop`, async (route) => {
      mock.sequences[mock.sequenceId] = {
        ...mock.sequences[mock.sequenceId],
        version: 2,
        duration_ms: 3000,
        updated_at: '2026-03-07T00:00:05.000Z',
        timeline_data: {
          ...mock.sequences[mock.sequenceId].timeline_data,
          duration_ms: 3000,
          layers: [imageLayer, generatedLayer],
        },
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          project_id: mock.projectId,
          skill: 'generate-telop',
          success: true,
          message: 'Added 1 telop clip(s) on a new layer.',
          changes: { telops_added: 1, layer_id: generatedLayer.id },
          duration_ms: 120,
        }),
      })
    })

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('timeline-audio-track-header-track-narration').click()
    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-generate-telop').click()

    await expect.poll(() => dialogMessages.at(-1)).toBe('Added 1 telop clip(s) on a new layer.')
    await expect(page.getByTestId(`video-layer-${generatedLayer.id}`)).toBeVisible()
    await expect(page.getByTestId(`preview-text-clip-${generatedTelopClip.id}`)).toBeVisible()

    const textZIndex = await page.getByTestId(`preview-text-clip-${generatedTelopClip.id}`).evaluate((element) => {
      return Number(window.getComputedStyle(element).zIndex)
    })
    const imageZIndex = await page.locator(`[data-clip-id="${imageOnlyClip.id}"]`).evaluate((element) => {
      const wrapper = element.closest('div.absolute')
      return wrapper ? Number(window.getComputedStyle(wrapper).zIndex) : Number.NEGATIVE_INFINITY
    })

    expect(textZIndex).toBeGreaterThan(imageZIndex)
  })

  test('keeps snap behavior when moving a video clip extended with freeze frame', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    const freezeExtendedClip: Clip = {
      id: 'clip-freeze-snap-a',
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 1000,
      in_point_ms: 0,
      out_point_ms: 1000,
      speed: 1,
      freeze_frame_ms: 1000,
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

    const targetClip: Clip = {
      id: 'clip-freeze-snap-b',
      asset_id: mock.primaryAssetId,
      start_ms: 5000,
      duration_ms: 1500,
      in_point_ms: 0,
      out_point_ms: 1500,
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

    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [freezeExtendedClip, targetClip]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 7000
    mock.projectDetails[mock.projectId].duration_ms = 7000
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = JSON.parse(JSON.stringify([freezeExtendedClip, targetClip]))
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 7000
    mock.sequences[mock.sequenceId].duration_ms = 7000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    const movingClip = page.getByTestId(`timeline-video-clip-${freezeExtendedClip.id}`)
    await expect(movingClip).toBeVisible()

    const clipBox = await movingClip.boundingBox()
    expect(clipBox).not.toBeNull()
    if (!clipBox) {
      throw new Error('Freeze-extended clip bounds were not available')
    }

    const pixelsPerMs = clipBox.width / (freezeExtendedClip.duration_ms + freezeExtendedClip.freeze_frame_ms)
    const intendedDeltaMsWithoutSnap = 3400
    const dragDistancePx = pixelsPerMs * intendedDeltaMsWithoutSnap

    await page.mouse.move(clipBox.x + clipBox.width / 2, clipBox.y + clipBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(clipBox.x + clipBox.width / 2 + dragDistancePx, clipBox.y + clipBox.height / 2, { steps: 10 })
    await page.mouse.up()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedClip = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips.find((clip) => clip.id === freezeExtendedClip.id)
    expect(updatedClip?.start_ms).toBe(3000)
    expect(updatedClip?.freeze_frame_ms).toBe(1000)
  })

  test('snaps only the selected clip to the previous clip end', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    const leadingClip: Clip = {
      id: 'clip-snap-prev-a',
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 1000,
      in_point_ms: 0,
      out_point_ms: 1000,
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

    const selectedClip: Clip = {
      id: 'clip-snap-prev-b',
      asset_id: mock.primaryAssetId,
      start_ms: 2000,
      duration_ms: 1000,
      in_point_ms: 0,
      out_point_ms: 1000,
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

    const trailingClip: Clip = {
      id: 'clip-snap-prev-c',
      asset_id: mock.primaryAssetId,
      start_ms: 4000,
      duration_ms: 1000,
      in_point_ms: 0,
      out_point_ms: 1000,
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

    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [leadingClip, selectedClip, trailingClip]
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 5000
    mock.projectDetails[mock.projectId].duration_ms = 5000
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = JSON.parse(JSON.stringify([leadingClip, selectedClip, trailingClip]))
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 5000
    mock.sequences[mock.sequenceId].duration_ms = 5000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId(`timeline-video-clip-${selectedClip.id}`).click()
    await page.getByTestId('timeline-snap-to-previous').click()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedClips = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips
    expect(updatedClips.find((clip) => clip.id === leadingClip.id)?.start_ms).toBe(0)
    expect(updatedClips.find((clip) => clip.id === selectedClip.id)?.start_ms).toBe(1000)
    expect(updatedClips.find((clip) => clip.id === trailingClip.id)?.start_ms).toBe(4000)
  })

  test('keeps Shift image resize snap from jumping to an oversized frame', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    const resizedImageClip: Clip = {
      id: 'clip-image-resize-a',
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 5000,
      in_point_ms: 0,
      out_point_ms: 5000,
      speed: 1,
      freeze_frame_ms: 0,
      transform: {
        x: -220,
        y: 0,
        width: 320,
        height: 180,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    const snapTargetImageClip: Clip = {
      id: 'clip-image-resize-b',
      asset_id: mock.primaryAssetId,
      start_ms: 0,
      duration_ms: 5000,
      in_point_ms: 0,
      out_point_ms: 5000,
      speed: 1,
      freeze_frame_ms: 0,
      transform: {
        x: 130,
        y: 0,
        width: 320,
        height: 180,
        scale: 1,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }

    mock.projectDetails[mock.projectId].timeline_data.layers[0].clips = [resizedImageClip]
    mock.projectDetails[mock.projectId].timeline_data.layers.push({
      id: 'layer-image-snap-target',
      name: 'Layer 2',
      type: 'content',
      order: 1,
      visible: true,
      locked: false,
      clips: [snapTargetImageClip],
    })
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 5000
    mock.projectDetails[mock.projectId].duration_ms = 5000
    mock.sequences[mock.sequenceId].timeline_data.layers[0].clips = JSON.parse(JSON.stringify([resizedImageClip]))
    mock.sequences[mock.sequenceId].timeline_data.layers.push({
      id: 'layer-image-snap-target',
      name: 'Layer 2',
      type: 'content',
      order: 1,
      visible: true,
      locked: false,
      clips: JSON.parse(JSON.stringify([snapTargetImageClip])),
    })
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 5000
    mock.sequences[mock.sequenceId].duration_ms = 5000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId(`timeline-video-clip-${resizedImageClip.id}`).click()
    const resizeHandle = page.getByTestId('preview-image-resize-br')
    await expect(resizeHandle).toBeVisible()

    const handleBox = await resizeHandle.boundingBox()
    expect(handleBox).not.toBeNull()
    if (!handleBox) {
      throw new Error('Image resize handle bounds were not available')
    }

    await page.keyboard.down('Shift')
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(handleBox.x + handleBox.width / 2 + 26, handleBox.y + handleBox.height / 2 + 14, { steps: 8 })
    await page.mouse.up()
    await page.keyboard.up('Shift')

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const updatedClip = mock.calls.sequenceUpdates[0].timelineData.layers
      .flatMap((layer) => layer.clips)
      .find((clip) => clip.id === resizedImageClip.id)
    expect(updatedClip).toBeTruthy()
    expect(updatedClip?.transform.width).toBeLessThan(420)
    expect(updatedClip?.transform.height).toBeLessThan(240)
    expect((updatedClip?.transform.width ?? 0) / (updatedClip?.transform.height ?? 1)).toBeCloseTo(320 / 180, 2)
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
    expect(addedShape?.fillColor).toBe('#ff2f72')
    expect(addedShape?.strokeColor).toBe('#ff2f72')
    expect(addedShape?.strokeWidth).toBe(0)
    expect(addedShape?.filled).toBe(true)
    const arrowPath = page.getByTestId('preview-container').getByTestId('shape-arrow-path')
    await expect(arrowPath).toHaveCount(1)
    await expect(arrowPath).toHaveAttribute('d', getArrowShapePath(180, 48))
    await expect(page.getByTestId('preview-container').locator('polygon')).toHaveCount(0)
    await expect(page.getByTestId('preview-container').locator('line')).toHaveCount(0)
    await expect(page.getByText(/Arrow|矢印/)).toBeVisible()
  })

  test('edits an arrow by dragging its start/end handles and keeps the result after reload', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-add-shape-arrow').click()
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const clipId = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips[0]?.id
    expect(clipId).toBeTruthy()

    await page.getByTestId(`timeline-video-clip-${clipId}`).click()
    await expect(page.getByTestId('shape-arrow-end-handle')).toBeVisible()

    const endHandle = page.getByTestId('shape-arrow-end-handle')
    const handleBox = await endHandle.boundingBox()
    expect(handleBox).not.toBeNull()
    if (!handleBox) {
      throw new Error('Arrow end handle bounds were not available')
    }

    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(handleBox.x + handleBox.width / 2 + 140, handleBox.y + handleBox.height / 2 - 60, { steps: 8 })
    await page.mouse.up()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)

    const updatedClip = mock.calls.sequenceUpdates[1].timelineData.layers[0].clips[0]
    expect(updatedClip?.shape?.type).toBe('arrow')
    expect(updatedClip?.shape?.width ?? 0).toBeGreaterThan(180)
    expect(Math.abs(updatedClip?.transform.rotation ?? 0)).toBeGreaterThan(5)
    expect(updatedClip?.transform.x ?? 0).not.toBe(0)
    expect(updatedClip?.transform.y ?? 0).not.toBe(0)
    const updatedArrowPath = await page.getByTestId('preview-container').getByTestId('shape-arrow-path').getAttribute('d')
    expect(updatedArrowPath).toBeTruthy()
    if (!updatedArrowPath) {
      throw new Error('Updated arrow path was not available')
    }
    const pathNumbers = updatedArrowPath.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? []
    const headLength = pathNumbers[6] - pathNumbers[4]
    expect(headLength).toBeCloseTo(getArrowHeadLength(updatedClip?.shape?.height ?? 48), 2)

    await page.reload()
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()
    await page.getByTestId('timeline-video-clip-' + clipId).click()
    await expect(page.getByTestId('shape-arrow-start-handle')).toBeVisible()
    await expect(page.getByTestId('shape-arrow-end-handle')).toBeVisible()
  })

  test('adjusts arrow thickness without exposing scale controls', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-add-shape-arrow').click()
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)

    const clipId = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips[0]?.id
    expect(clipId).toBeTruthy()

    await page.getByTestId(`timeline-video-clip-${clipId}`).click()
    const propertyRail = page.getByTestId('editor-property-rail')
    if (await propertyRail.isVisible().catch(() => false)) {
      await propertyRail.click()
    }
    await expect(page.getByTestId('right-panel')).toBeVisible()
    await expect(page.getByTestId('video-scale-input')).toHaveCount(0)
    await expect(page.getByTestId('video-scale-slider')).toHaveCount(0)
    await expect(page.getByTestId('arrow-scale-locked-note')).toBeVisible()

    await page.getByTestId('shape-arrow-thickness-input').fill('72')

    await expect.poll(() => {
      const clip = mock.sequences[mock.sequenceId].timeline_data.layers[0].clips[0]
      return clip?.shape?.height
    }).toBe(72)

    const updatedClip = mock.sequences[mock.sequenceId].timeline_data.layers[0].clips[0]
    expect(updatedClip?.shape?.type).toBe('arrow')
    expect(updatedClip?.shape?.width ?? 0).toBeGreaterThanOrEqual(207)
    expect(updatedClip?.transform.scale).toBe(1)
    await expect(page.getByTestId('preview-container').getByTestId('shape-arrow-path')).toHaveAttribute('d', getArrowShapePath(updatedClip?.shape?.width ?? 207, 72))

    await page.reload()
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()
    await page.getByTestId(`timeline-video-clip-${clipId}`).click()
    if (await propertyRail.isVisible().catch(() => false)) {
      await propertyRail.click()
    }
    await expect(page.getByTestId('shape-arrow-thickness-input')).toHaveValue('72')
    await expect(page.getByTestId('video-scale-input')).toHaveCount(0)
  })

  test('shows a shared preview rotate handle for shape, image, and video clips and persists image rotation', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const videoAsset: Asset = {
      id: 'asset-video-1',
      project_id: mock.projectId,
      name: 'Mock Video',
      type: 'video',
      subtype: 'mock',
      storage_key: 'mock/video.mp4',
      storage_url: '/lp/lp_video_en.mp4',
      thumbnail_url: null,
      duration_ms: 4000,
      width: 1280,
      height: 720,
      file_size: 1024,
      mime_type: 'video/mp4',
      chroma_key_color: null,
      hash: null,
      folder_id: null,
      created_at: '2026-03-07T00:00:00.000Z',
      metadata: null,
    }
    mock.assetsByProject[mock.projectId].push(videoAsset)
    const seededVideoClip: Clip = {
      id: 'clip-seeded-video-rotate',
      asset_id: videoAsset.id,
      start_ms: 500,
      duration_ms: 3000,
      in_point_ms: 0,
      out_point_ms: null,
      speed: 1,
      freeze_frame_ms: 0,
      transform: {
        x: 120,
        y: 0,
        width: null,
        height: null,
        scale: 0.4,
        rotation: 0,
      },
      effects: {
        opacity: 1,
      },
    }
    mock.projectDetails[mock.projectId].timeline_data.layers.push({
      id: 'layer-video-seeded',
      name: 'Layer 2',
      type: 'content',
      order: 1,
      visible: true,
      locked: false,
      clips: [seededVideoClip],
    })
    mock.projectDetails[mock.projectId].timeline_data.duration_ms = 4000
    mock.projectDetails[mock.projectId].duration_ms = 4000
    mock.sequences[mock.sequenceId].timeline_data.layers.push({
      id: 'layer-video-seeded',
      name: 'Layer 2',
      type: 'content',
      order: 1,
      visible: true,
      locked: false,
      clips: [seededVideoClip],
    })
    mock.sequences[mock.sequenceId].timeline_data.duration_ms = 4000
    mock.sequences[mock.sequenceId].duration_ms = 4000

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.locator('[data-menu-id="add"] button').first().click()
    await page.getByTestId('timeline-add-shape-rectangle').click()
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(1)
    const shapeClipId = mock.calls.sequenceUpdates[0].timelineData.layers[0].clips[0]?.id
    expect(shapeClipId).toBeTruthy()
    await page.getByTestId(`timeline-video-clip-${shapeClipId}`).click()
    await expect(page.getByTestId('preview-rotate-handle')).toBeVisible()

    await dragAssetToVideoLayer(page, {
      assetId: mock.primaryAssetId,
      layerId: 'layer-1',
      offsetX: 240,
    })
    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(2)
    const imageClipId = mock.calls.sequenceUpdates[1].timelineData.layers[0].clips.at(-1)?.id
    expect(imageClipId).toBeTruthy()
    await page.getByTestId(`timeline-video-clip-${imageClipId}`).click()
    await expect(page.getByTestId('preview-rotate-handle')).toBeVisible()

    const rotateHandle = page.getByTestId('preview-rotate-handle')
    const rotateHandleBox = await rotateHandle.boundingBox()
    expect(rotateHandleBox).not.toBeNull()
    if (!rotateHandleBox) {
      throw new Error('Preview rotate handle bounds were not available')
    }
    await page.mouse.move(rotateHandleBox.x + rotateHandleBox.width / 2, rotateHandleBox.y + rotateHandleBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(rotateHandleBox.x + rotateHandleBox.width / 2 + 80, rotateHandleBox.y + rotateHandleBox.height / 2 + 30, { steps: 8 })
    await page.mouse.up()

    await expect.poll(() => mock.calls.sequenceUpdates.length).toBe(3)
    const rotatedImageClip = mock.calls.sequenceUpdates[2].timelineData.layers[0].clips.find((clip) => clip.id === imageClipId)
    expect(Math.abs(rotatedImageClip?.transform.rotation ?? 0)).toBeGreaterThan(5)

    await page.getByTestId(`timeline-video-clip-${seededVideoClip.id}`).click()
    await expect(page.getByTestId('preview-rotate-handle')).toBeVisible()

    await page.reload()
    await page.getByTestId('editor-header').waitFor()
    await page.getByTestId('timeline-area').waitFor()
    await page.getByTestId(`timeline-video-clip-${imageClipId}`).click()
    await expect(page.getByTestId('preview-rotate-handle')).toBeVisible()
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

  test('releases the sequence lock when leaving the editor', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)

    await openSeededEditor(page, mock.projectId, mock.sequenceId)

    await page.getByTestId('editor-open-exit-confirm').click()
    await expect(page.getByTestId('editor-confirm-exit')).toBeVisible()
    await page.getByTestId('editor-confirm-exit').click()

    await expect(page).toHaveURL(/\/app$/)
    await expect.poll(() => mock.calls.sequenceUnlocks.length).toBe(1)
    expect(mock.calls.sequenceUnlocks[0]).toEqual({
      projectId: mock.projectId,
      sequenceId: mock.sequenceId,
    })
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
