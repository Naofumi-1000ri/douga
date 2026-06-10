import { describe, it, expect } from 'vitest'
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
