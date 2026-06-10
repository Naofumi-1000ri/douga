import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import ColorRgbInput from './ColorRgbInput'
import { applyLinkedDelta } from './colorRgbUtils'

describe('applyLinkedDelta', () => {
  const base = { r: 100, g: 150, b: 200 }

  describe('linked=false (独立モード)', () => {
    it('R チャンネルのみ変更する', () => {
      const result = applyLinkedDelta(base, 'r', 120, false)
      expect(result).toEqual({ r: 120, g: 150, b: 200 })
    })

    it('G チャンネルのみ変更する', () => {
      const result = applyLinkedDelta(base, 'g', 80, false)
      expect(result).toEqual({ r: 100, g: 80, b: 200 })
    })

    it('B チャンネルのみ変更する', () => {
      const result = applyLinkedDelta(base, 'b', 50, false)
      expect(result).toEqual({ r: 100, g: 150, b: 50 })
    })

    it('0 以下はクランプされる', () => {
      const result = applyLinkedDelta(base, 'r', -10, false)
      expect(result.r).toBe(0)
    })

    it('255 超はクランプされる', () => {
      const result = applyLinkedDelta(base, 'g', 300, false)
      expect(result.g).toBe(255)
    })
  })

  describe('linked=true (連動モード)', () => {
    it('R +20 のとき G も B も +20 される', () => {
      const result = applyLinkedDelta(base, 'r', 120, true)
      expect(result).toEqual({ r: 120, g: 170, b: 220 })
    })

    it('G -50 のとき R も B も -50 される', () => {
      const result = applyLinkedDelta(base, 'g', 100, true)
      expect(result).toEqual({ r: 50, g: 100, b: 150 })
    })

    it('連動時に 255 を超える場合はクランプされる', () => {
      // b=200, delta=+60 → r=160, g=210→255, b=260→255
      const result = applyLinkedDelta(base, 'b', 260, true)
      expect(result.r).toBe(160)
      expect(result.g).toBe(210)
      expect(result.b).toBe(255)
    })

    it('連動時に 0 を下回る場合はクランプされる', () => {
      // r=100, newVal=10, delta=-90 → r=10, g=60, b=110
      const result = applyLinkedDelta(base, 'r', 10, true)
      expect(result).toEqual({ r: 10, g: 60, b: 110 })
    })

    it('連動時に delta=0 のとき全チャンネルが変わらない', () => {
      const result = applyLinkedDelta(base, 'r', 100, true)
      expect(result).toEqual(base)
    })

    it('全チャンネルが 0 でも delta 適用後クランプされる', () => {
      const allZero = { r: 0, g: 0, b: 0 }
      // B を -10 にしようとする → delta = -10 → R=-10→0, G=-10→0, B=-10→0
      const result = applyLinkedDelta(allZero, 'b', -10, true)
      expect(result).toEqual({ r: 0, g: 0, b: 0 })
    })

    it('全チャンネルが 255 でも delta 適用後クランプされる', () => {
      const allMax = { r: 255, g: 255, b: 255 }
      // R を 265 にしようとする → delta = +10 → 全チャンネル +10 → クランプ 255
      const result = applyLinkedDelta(allMax, 'r', 265, true)
      expect(result).toEqual({ r: 255, g: 255, b: 255 })
    })
  })

  describe('端数処理', () => {
    it('小数値は四捨五入される', () => {
      const result = applyLinkedDelta(base, 'r', 100.7, false)
      expect(result.r).toBe(101)
    })

    it('連動時の小数値も四捨五入される', () => {
      const result = applyLinkedDelta(base, 'r', 100.6, true)
      // delta = 0.6 → G=150.6→151, B=200.6→201, R=100.6→101
      expect(result.r).toBe(101)
      expect(result.g).toBe(151)
      expect(result.b).toBe(201)
    })
  })
})

/**
 * PR #333 レビュー対応 (MEDIUM): onCommit 後に debounce タイマーが遅延発火して
 * undo 履歴が同値で二重に積まれる退行の回帰テスト。
 */
describe('ColorRgbInput debounce cancellation (PR #333 review)', () => {
  let onChangeDebounced: ReturnType<typeof vi.fn<(hex: string) => void>>
  let onCommit: ReturnType<typeof vi.fn<(hex: string) => void>>

  beforeEach(() => {
    vi.useFakeTimers()
    onChangeDebounced = vi.fn<(hex: string) => void>()
    onCommit = vi.fn<(hex: string) => void>()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  /**
   * 実アプリの結線と同じく onChangeLocal で value を即時反映する stateful ラッパー。
   * (Editor 側では handleUpdateVideoClipLocal が store を即時更新して value prop が変わる)
   */
  function StatefulColorInput({
    initial,
    changeDebounced,
    commit,
  }: {
    initial: string
    changeDebounced: (hex: string) => void
    commit: (hex: string) => void
  }) {
    const [color, setColor] = useState(initial)
    return (
      <ColorRgbInput
        value={color}
        onChangeLocal={setColor}
        onChangeDebounced={changeDebounced}
        onCommit={commit}
      />
    )
  }

  const renderInput = (value = '#64c8ff') =>
    render(
      <StatefulColorInput initial={value} changeDebounced={onChangeDebounced} commit={onCommit} />,
    )

  it('ピッカー変更→blur(commit)後、debounce タイマーが発火しない(二重コミット防止)', () => {
    renderInput()
    const picker = screen.getByTestId('color-rgb-picker')

    fireEvent.change(picker, { target: { value: '#ff0000' } })
    fireEvent.blur(picker)

    // blur 時点で onCommit が 1 回だけ呼ばれる
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith('#ff0000')

    // タイマーを進めても debounce コールバックは発火しない(キャンセル済み)
    vi.advanceTimersByTime(1000)
    expect(onChangeDebounced).not.toHaveBeenCalled()
    expect(onCommit).toHaveBeenCalledTimes(1)
  })

  it('変更後 300ms 経過で onChangeDebounced が 1 回発火し、その後の blur では onCommit を呼ばない', () => {
    renderInput()
    const picker = screen.getByTestId('color-rgb-picker')

    fireEvent.change(picker, { target: { value: '#ff0000' } })
    vi.advanceTimersByTime(300)
    expect(onChangeDebounced).toHaveBeenCalledTimes(1)
    expect(onChangeDebounced).toHaveBeenCalledWith('#ff0000')

    // debounce 発火済み(commit 経路に乗っている)なら blur で onCommit しない
    fireEvent.blur(picker)
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('変更なしで blur した場合は何も呼ばれない', () => {
    renderInput()
    const picker = screen.getByTestId('color-rgb-picker')

    fireEvent.blur(picker)
    vi.advanceTimersByTime(1000)

    expect(onCommit).not.toHaveBeenCalled()
    expect(onChangeDebounced).not.toHaveBeenCalled()
  })

  it('連続変更はタイマーがリセットされ、最後の値だけが debounce 発火する', () => {
    renderInput()
    const picker = screen.getByTestId('color-rgb-picker')

    fireEvent.change(picker, { target: { value: '#ff0000' } })
    vi.advanceTimersByTime(200)
    fireEvent.change(picker, { target: { value: '#00ff00' } })
    vi.advanceTimersByTime(200)
    // 1回目の変更から400ms経過しているが、リセットされたためまだ発火していない
    expect(onChangeDebounced).not.toHaveBeenCalled()

    vi.advanceTimersByTime(100)
    expect(onChangeDebounced).toHaveBeenCalledTimes(1)
    expect(onChangeDebounced).toHaveBeenCalledWith('#00ff00')
  })

  it('アンマウント時に未発火タイマーが破棄される', () => {
    const { unmount } = renderInput()
    const picker = screen.getByTestId('color-rgb-picker')

    fireEvent.change(picker, { target: { value: '#ff0000' } })
    unmount()
    vi.advanceTimersByTime(1000)

    expect(onChangeDebounced).not.toHaveBeenCalled()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('hex テキスト変更→blur(commit)後、debounce タイマーが発火しない', () => {
    renderInput()
    const hexInput = screen.getByTestId('color-rgb-hex')

    fireEvent.change(hexInput, { target: { value: '#123456' } })
    fireEvent.blur(hexInput)

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith('#123456')

    vi.advanceTimersByTime(1000)
    expect(onChangeDebounced).not.toHaveBeenCalled()
    expect(onCommit).toHaveBeenCalledTimes(1)
  })

  it('RGB 数値入力の Enter 確定後、debounce タイマーが発火しない', () => {
    renderInput('#64c8ff') // R=100, G=200, B=255
    const rInput = screen.getByTestId('color-rgb-r')

    // NumericInput の onChange → handleChannelChange → scheduleDebounced
    fireEvent.change(rInput, { target: { value: '200' } })
    // Enter → NumericInput.commit → handleChannelCommit → cancel + onCommit
    fireEvent.keyDown(rInput, { key: 'Enter' })

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith('#c8c8ff')

    vi.advanceTimersByTime(1000)
    expect(onChangeDebounced).not.toHaveBeenCalled()
    expect(onCommit).toHaveBeenCalledTimes(1)
  })
})
