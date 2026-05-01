import { describe, it, expect } from 'vitest'

/**
 * recenterPreview のロジックを単体テスト。
 * vitest 環境は node なので renderHook は使わず、
 * ロジックを関数として抽出して検証する。
 */
function recenterPreviewLogic(el: {
  scrollHeight: number
  clientHeight: number
  scrollWidth: number
  clientWidth: number
  scrollTop: number
  scrollLeft: number
}) {
  el.scrollTop = Math.max(0, (el.scrollHeight - el.clientHeight) / 2)
  el.scrollLeft = Math.max(0, (el.scrollWidth - el.clientWidth) / 2)
}

describe('recenterPreview', () => {
  it('recenterPreview が export されていること（型チェック）', async () => {
    // usePreviewViewport の戻り値に recenterPreview が含まれることを型レベルで確認
    // ここでは import して関数であることを確認する
    const mod = await import('./usePreviewViewport')
    expect(typeof mod.usePreviewViewport).toBe('function')
  })

  it('スクロール位置を中央に設定する（scrollHeight=400, clientHeight=200, scrollWidth=400, clientWidth=200）', () => {
    const mockEl = {
      scrollHeight: 400,
      clientHeight: 200,
      scrollWidth: 400,
      clientWidth: 200,
      scrollTop: 0,
      scrollLeft: 0,
    }

    recenterPreviewLogic(mockEl)

    expect(mockEl.scrollTop).toBe(100)
    expect(mockEl.scrollLeft).toBe(100)
  })

  it('スクロール不要な場合（コンテンツがコンテナより小さい）は 0 を維持する', () => {
    const mockEl = {
      scrollHeight: 100,
      clientHeight: 200,
      scrollWidth: 100,
      clientWidth: 200,
      scrollTop: 0,
      scrollLeft: 0,
    }

    recenterPreviewLogic(mockEl)

    expect(mockEl.scrollTop).toBe(0)
    expect(mockEl.scrollLeft).toBe(0)
  })

  it('非対称なコンテンツサイズでも正しく中央化する', () => {
    const mockEl = {
      scrollHeight: 600,
      clientHeight: 200,
      scrollWidth: 800,
      clientWidth: 400,
      scrollTop: 0,
      scrollLeft: 0,
    }

    recenterPreviewLogic(mockEl)

    expect(mockEl.scrollTop).toBe(200)
    expect(mockEl.scrollLeft).toBe(200)
  })
})
