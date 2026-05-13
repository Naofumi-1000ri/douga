/**
 * ImageClipThumbnails の自己修復テスト (#252)
 *
 * - img の onError が発火すると 'douga-assets-changed' が dispatch される
 * - data-retried='1' の場合は 2 度目は dispatch されない (無限ループ防止)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, fireEvent } from '@testing-library/react'

import ImageClipThumbnails from '../ImageClipThumbnails'

const EXPIRED_URL =
  'https://storage.googleapis.com/bucket/image.jpg?X-Goog-Date=20200101T000000Z&X-Goog-Expires=345600&X-Goog-Signature=xyz'

beforeEach(() => {
  vi.clearAllMocks()
})

function renderComponent(url = EXPIRED_URL) {
  return render(
    <ImageClipThumbnails
      imageUrl={url}
      assetId="asset-img-001"
      clipWidth={200}
      clipHeight={40}
    />,
  )
}

describe('ImageClipThumbnails 自己修復 (#252)', () => {
  it('img onError → "douga-assets-changed" が dispatch される', () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { container } = renderComponent()
    const imgs = container.querySelectorAll('img')
    expect(imgs.length).toBeGreaterThan(0)

    act(() => {
      fireEvent.error(imgs[0])
    })

    const dispatched = dispatchSpy.mock.calls.some(
      ([e]) => e instanceof CustomEvent && e.type === 'douga-assets-changed',
    )
    expect(dispatched).toBe(true)
  })

  it('data-retried="1" の場合は 2 度目は dispatch されない', () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { container } = renderComponent()
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

  it('複数の img があっても最初の onError だけが dispatch する (各 img 独立)', () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { container } = renderComponent()
    const imgs = container.querySelectorAll('img')
    // 2 枚以上あることが前提 (clipWidth=200, thumbWidth≈71)
    expect(imgs.length).toBeGreaterThanOrEqual(2)

    // 最初の img を 2 回 error (2 回目は retried=1 なのでスキップ)
    act(() => {
      fireEvent.error(imgs[0])
      fireEvent.error(imgs[0])
    })

    const count = dispatchSpy.mock.calls.filter(
      ([e]) => e instanceof CustomEvent && e.type === 'douga-assets-changed',
    ).length
    expect(count).toBe(1)
  })
})
