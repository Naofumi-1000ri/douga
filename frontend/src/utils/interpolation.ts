/**
 * Interpolation utilities with easing functions.
 *
 * Provides a Remotion-compatible interpolate() function and easing library.
 * Used by the frontend preview to match backend FFmpeg rendering behavior.
 *
 * Usage:
 *   import { interpolate, Easing } from '@/utils/interpolation'
 *
 *   const value = interpolate(frame, [0, 100], [0, 1], {
 *     easing: Easing.easeInOut,
 *   })
 */

// =============================================================================
// Easing Functions
// =============================================================================

export type EasingFunction = (t: number) => number

export const Easing = {
  linear: (t: number): number => t,

  easeIn: (t: number): number => t * t * t,

  easeOut: (t: number): number => 1 - (1 - t) ** 3,

  easeInOut: (t: number): number =>
    t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2,

  easeInQuad: (t: number): number => t * t,

  easeOutQuad: (t: number): number => 1 - (1 - t) * (1 - t),

  easeInOutQuad: (t: number): number =>
    t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2,

  easeInSine: (t: number): number => 1 - Math.cos((t * Math.PI) / 2),

  easeOutSine: (t: number): number => Math.sin((t * Math.PI) / 2),

  easeInOutSine: (t: number): number => -(Math.cos(Math.PI * t) - 1) / 2,

  easeInExpo: (t: number): number => (t === 0 ? 0 : 2 ** (10 * t - 10)),

  easeOutExpo: (t: number): number => (t === 1 ? 1 : 1 - 2 ** (-10 * t)),

  easeInOutExpo: (t: number): number => {
    if (t === 0) return 0
    if (t === 1) return 1
    return t < 0.5 ? 2 ** (20 * t - 10) / 2 : (2 - 2 ** (-20 * t + 10)) / 2
  },

  easeInBack: (t: number): number => {
    const c1 = 1.70158
    const c3 = c1 + 1
    return c3 * t * t * t - c1 * t * t
  },

  easeOutBack: (t: number): number => {
    const c1 = 1.70158
    const c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2
  },

  easeInOutBack: (t: number): number => {
    const c1 = 1.70158
    const c2 = c1 * 1.525
    return t < 0.5
      ? ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2
      : ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2
  },

  /**
   * Create a cubic bezier easing function.
   */
  bezier:
    (x1: number, y1: number, x2: number, y2: number): EasingFunction =>
    (t: number): number => {
      const epsilon = 1e-6
      let tApprox = t

      for (let i = 0; i < 8; i++) {
        const x =
          3 * (1 - tApprox) ** 2 * tApprox * x1 +
          3 * (1 - tApprox) * tApprox ** 2 * x2 +
          tApprox ** 3
        if (Math.abs(x - t) < epsilon) break

        const dx =
          3 * (1 - tApprox) ** 2 * x1 +
          6 * (1 - tApprox) * tApprox * (x2 - x1) +
          3 * tApprox ** 2 * (1 - x2)
        if (Math.abs(dx) < epsilon) break

        tApprox -= (x - t) / dx
      }

      return (
        3 * (1 - tApprox) ** 2 * tApprox * y1 +
        3 * (1 - tApprox) * tApprox ** 2 * y2 +
        tApprox ** 3
      )
    },
} as const

// CSS easing presets
export const CSSEasing = {
  ease: Easing.bezier(0.25, 0.1, 0.25, 1.0),
  easeIn: Easing.bezier(0.42, 0, 1.0, 1.0),
  easeOut: Easing.bezier(0, 0, 0.58, 1.0),
  easeInOut: Easing.bezier(0.42, 0, 0.58, 1.0),
} as const

// Easing name -> function lookup for string-based configuration
const EASING_FUNCTIONS: Record<string, EasingFunction> = {
  linear: Easing.linear,
  ease_in: Easing.easeIn,
  ease_out: Easing.easeOut,
  ease_in_out: Easing.easeInOut,
  ease_in_quad: Easing.easeInQuad,
  ease_out_quad: Easing.easeOutQuad,
  ease_in_out_quad: Easing.easeInOutQuad,
  ease_in_sine: Easing.easeInSine,
  ease_out_sine: Easing.easeOutSine,
  ease_in_out_sine: Easing.easeInOutSine,
  ease_in_expo: Easing.easeInExpo,
  ease_out_expo: Easing.easeOutExpo,
  ease_in_out_expo: Easing.easeInOutExpo,
  ease_in_back: Easing.easeInBack,
  ease_out_back: Easing.easeOutBack,
  ease_in_out_back: Easing.easeInOutBack,
}

export function getEasingFunction(name: string): EasingFunction {
  const fn = EASING_FUNCTIONS[name]
  if (!fn) {
    console.warn(`Unknown easing function: ${name}, falling back to linear`)
    return Easing.linear
  }
  return fn
}

// =============================================================================
// Core Interpolation
// =============================================================================

export type ExtrapolateType = 'clamp' | 'extend' | 'identity'

export interface InterpolateOptions {
  easing?: EasingFunction
  extrapolateLeft?: ExtrapolateType
  extrapolateRight?: ExtrapolateType
}

/**
 * Interpolate a value based on input/output ranges with optional easing.
 *
 * Equivalent to Remotion's interpolate() function.
 *
 * @example
 * // Simple 0-1 mapping
 * interpolate(50, [0, 100], [0, 1]) // -> 0.5
 *
 * // With easing
 * interpolate(50, [0, 100], [0, 1], { easing: Easing.easeInOut })
 *
 * // Multi-point
 * interpolate(75, [0, 50, 100], [0, 1, 0]) // -> 0.5
 */
export function interpolate(
  frame: number,
  inputRange: number[],
  outputRange: number[],
  options: InterpolateOptions = {}
): number {
  const {
    easing = Easing.linear,
    extrapolateLeft = 'clamp',
    extrapolateRight = 'clamp',
  } = options

  if (inputRange.length !== outputRange.length) {
    throw new Error('inputRange and outputRange must have the same length')
  }
  if (inputRange.length < 2) {
    throw new Error('inputRange must have at least 2 values')
  }

  // Before range
  if (frame <= inputRange[0]) {
    if (extrapolateLeft === 'clamp') return outputRange[0]
    if (extrapolateLeft === 'identity') return frame
  }

  // After range
  if (frame >= inputRange[inputRange.length - 1]) {
    if (extrapolateRight === 'clamp') return outputRange[outputRange.length - 1]
    if (extrapolateRight === 'identity') return frame
  }

  // Find the correct segment
  let segmentIdx = 0
  for (let i = 1; i < inputRange.length; i++) {
    if (frame <= inputRange[i]) {
      segmentIdx = i - 1
      break
    }
    if (i === inputRange.length - 1) {
      segmentIdx = inputRange.length - 2
    }
  }

  // Calculate t within this segment
  const segStart = inputRange[segmentIdx]
  const segEnd = inputRange[segmentIdx + 1]
  const segRange = segEnd - segStart

  const t = segRange === 0 ? 0 : (frame - segStart) / segRange

  // Apply easing
  const tEased = easing(t)

  // Interpolate output
  const outStart = outputRange[segmentIdx]
  const outEnd = outputRange[segmentIdx + 1]

  return outStart + (outEnd - outStart) * tEased
}
