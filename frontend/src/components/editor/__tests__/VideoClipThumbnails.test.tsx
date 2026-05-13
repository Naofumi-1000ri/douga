/**
 * VideoClipThumbnails の自己修復テスト (#252)
 *
 * - img の onError が発火すると 'douga-assets-changed' が dispatch される
 * - data-retried='1' の場合は 2 度目は dispatch されない (無限ループ防止)
 * - 'douga-assets-changed' 受信時に再 fetch が行われる
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, fireEvent, waitFor } from '@testing-library/react'

// ── assetsApi モック ──────────────────────────────────────────────────────────
const { getGridThumbnailsMock, generatePriorityMock } = vi.hoisted(() => ({
  getGridThumbnailsMock: vi.fn(),
  generatePriorityMock: vi.fn(),
}))

vi.mock('@/api/assets', () => ({
  assetsApi: {
    getGridThumbnails: getGridThumbnailsMock,
    generatePriorityThumbnails: generatePriorityMock,
  },
}))

import VideoClipThumbnails from '../VideoClipThumbnails'

const PROJECT_ID = 'proj-test'
const ASSET_ID = 'asset-001'

// Expired signed URL stub (includes X-Goog-Date)
const EXPIRED_URL =
  'https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Date=20200101T000000Z&X-Goog-Expires=345600&X-Goog-Signature=abc'

function makeGridResponse(url: string) {
  return { thumbnails: { 0: url } }
}

beforeEach(() => {
  vi.clearAllMocks()
  getGridThumbnailsMock.mockResolvedValue(makeGridResponse(EXPIRED_URL))
  generatePriorityMock.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderComponent() {
  return render(
    <VideoClipThumbnails
      projectId={PROJECT_ID}
      assetId={ASSET_ID}
      clipWidth={200}
      inPointMs={0}
      durationMs={3000}
      speed={1}
      clipHeight={40}
    />,
  )
}

describe('VideoClipThumbnails 自己修復 (#252)', () => {
  it('img onError → "douga-assets-changed" が dispatch される', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { container } = renderComponent()

    // img が表示されるまで待つ (fetch + sequential reveal animation)
    await waitFor(() => {
      expect(container.querySelector('img')).not.toBeNull()
    }, { timeout: 500 })

    const img = container.querySelector('img')!

    // img の onError を発火
    act(() => {
      fireEvent.error(img)
    })

    const dispatched = dispatchSpy.mock.calls.some(
      ([e]) => e instanceof CustomEvent && e.type === 'douga-assets-changed',
    )
    expect(dispatched).toBe(true)
  })

  it('data-retried="1" の場合は 2 度目は dispatch されない', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { container } = renderComponent()

    await waitFor(() => {
      expect(container.querySelector('img')).not.toBeNull()
    }, { timeout: 500 })

    const img = container.querySelector('img')!
    img.dataset.retried = '1'

    act(() => {
      fireEvent.error(img)
    })

    const dispatched = dispatchSpy.mock.calls.some(
      ([e]) => e instanceof CustomEvent && e.type === 'douga-assets-changed',
    )
    expect(dispatched).toBe(false)
  })

  it('"douga-assets-changed" 受信でキャッシュがクリアされて再 fetch が行われる', async () => {
    renderComponent()

    // 初回 fetch 完了まで待つ
    await waitFor(() => {
      expect(getGridThumbnailsMock).toHaveBeenCalled()
    }, { timeout: 500 })

    const callCountBefore = getGridThumbnailsMock.mock.calls.length
    expect(callCountBefore).toBeGreaterThan(0)

    // 外部から 'douga-assets-changed' を dispatch
    await act(async () => {
      window.dispatchEvent(new CustomEvent('douga-assets-changed'))
      await new Promise(r => setTimeout(r, 100))
    })

    // 再 fetch が行われていること
    expect(getGridThumbnailsMock.mock.calls.length).toBeGreaterThan(callCountBefore)
  })
})
