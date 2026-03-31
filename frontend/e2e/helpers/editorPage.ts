import type { Page } from '@playwright/test'

export async function openSeededEditor(page: Page, projectId: string, sequenceId: string) {
  await page.goto(`/project/${projectId}/sequence/${sequenceId}`)
  await page.waitForLoadState('networkidle')
  await page.getByTestId('editor-header').waitFor()
  await page.getByTestId('left-panel').waitFor()
  await page.getByTestId('timeline-area').waitFor()
}

export async function dragAssetToVideoLayer(
  page: Page,
  options: {
    assetId: string
    layerId: string
    offsetX?: number
  }
) {
  const asset = page.getByTestId(`asset-item-${options.assetId}`)
  const layer = page.getByTestId(`video-layer-${options.layerId}`)
  const layerBox = await layer.boundingBox()

  if (!layerBox) {
    throw new Error(`Could not resolve bounds for layer ${options.layerId}`)
  }

  const dataTransfer = await page.evaluateHandle(() => new DataTransfer())

  await asset.dispatchEvent('dragstart', { dataTransfer })
  await layer.dispatchEvent('dragover', {
    dataTransfer,
    clientX: layerBox.x + (options.offsetX ?? 180),
    clientY: layerBox.y + layerBox.height / 2,
  })
  await layer.dispatchEvent('drop', {
    dataTransfer,
    clientX: layerBox.x + (options.offsetX ?? 180),
    clientY: layerBox.y + layerBox.height / 2,
  })
}

export async function dragAssetToAudioTrack(
  page: Page,
  options: {
    assetId: string
    trackId: string
    offsetX?: number
  }
) {
  const asset = page.getByTestId(`asset-item-${options.assetId}`)
  const track = page.getByTestId(`timeline-audio-track-row-${options.trackId}`)
  const trackBox = await track.boundingBox()

  if (!trackBox) {
    throw new Error(`Could not resolve bounds for track ${options.trackId}`)
  }

  const dataTransfer = await page.evaluateHandle(() => new DataTransfer())

  await asset.dispatchEvent('dragstart', { dataTransfer })
  await track.dispatchEvent('dragover', {
    dataTransfer,
    clientX: trackBox.x + (options.offsetX ?? 180),
    clientY: trackBox.y + trackBox.height / 2,
  })
  await track.dispatchEvent('drop', {
    dataTransfer,
    clientX: trackBox.x + (options.offsetX ?? 180),
    clientY: trackBox.y + trackBox.height / 2,
  })
}
