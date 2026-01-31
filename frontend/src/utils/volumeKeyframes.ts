import type { VolumeKeyframe, AudioClip } from '@/store/projectStore'

/**
 * Linear interpolation between two values
 */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * Get interpolated volume at a specific time within a clip
 * @param clip The audio clip containing volume keyframes
 * @param timeInClipMs Time relative to clip start (0 = clip start)
 * @returns Interpolated volume value (0.0 - 1.0)
 */
export function getInterpolatedVolume(
  clip: AudioClip,
  timeInClipMs: number
): number {
  const keyframes = clip.volume_keyframes

  // No keyframes - return clip's base volume
  if (!keyframes || keyframes.length === 0) {
    return clip.volume
  }

  // Sort keyframes by time
  const sortedKeyframes = [...keyframes].sort((a, b) => a.time_ms - b.time_ms)

  // Before first keyframe - use first keyframe value
  if (timeInClipMs <= sortedKeyframes[0].time_ms) {
    return sortedKeyframes[0].value
  }

  // After last keyframe - use last keyframe value
  if (timeInClipMs >= sortedKeyframes[sortedKeyframes.length - 1].time_ms) {
    return sortedKeyframes[sortedKeyframes.length - 1].value
  }

  // Find surrounding keyframes
  let prevKeyframe: VolumeKeyframe | null = null
  let nextKeyframe: VolumeKeyframe | null = null

  for (let i = 0; i < sortedKeyframes.length - 1; i++) {
    if (
      timeInClipMs >= sortedKeyframes[i].time_ms &&
      timeInClipMs < sortedKeyframes[i + 1].time_ms
    ) {
      prevKeyframe = sortedKeyframes[i]
      nextKeyframe = sortedKeyframes[i + 1]
      break
    }
  }

  // Safety fallback
  if (!prevKeyframe || !nextKeyframe) {
    return clip.volume
  }

  // Calculate interpolation factor (0-1)
  const duration = nextKeyframe.time_ms - prevKeyframe.time_ms
  const elapsed = timeInClipMs - prevKeyframe.time_ms
  const t = duration > 0 ? elapsed / duration : 0

  // Linear interpolation
  return lerp(prevKeyframe.value, nextKeyframe.value, t)
}

/**
 * Add or update a volume keyframe at the specified time
 * @param keyframes Existing keyframes array (or undefined)
 * @param timeMs Time in milliseconds (relative to clip start)
 * @param value Volume value (0.0 - 1.0)
 * @returns Updated keyframes array
 */
export function addVolumeKeyframe(
  keyframes: VolumeKeyframe[] | undefined,
  timeMs: number,
  value: number
): VolumeKeyframe[] {
  const kfs = keyframes ? [...keyframes] : []

  // Clamp value to valid range
  const clampedValue = Math.max(0, Math.min(1, value))

  // Check if keyframe exists at this time (within 50ms tolerance)
  const existingIndex = kfs.findIndex(
    (kf) => Math.abs(kf.time_ms - timeMs) < 50
  )

  const newKeyframe: VolumeKeyframe = {
    time_ms: Math.round(timeMs),
    value: clampedValue,
  }

  if (existingIndex >= 0) {
    // Update existing keyframe
    kfs[existingIndex] = newKeyframe
  } else {
    // Add new keyframe
    kfs.push(newKeyframe)
  }

  // Sort by time and remove duplicates
  return kfs
    .sort((a, b) => a.time_ms - b.time_ms)
    .filter((kf, i, arr) => i === 0 || kf.time_ms !== arr[i - 1].time_ms)
}

/**
 * Remove a volume keyframe at the specified time
 * @param keyframes Existing keyframes array
 * @param timeMs Time in milliseconds to remove
 * @returns Updated keyframes array
 */
export function removeVolumeKeyframe(
  keyframes: VolumeKeyframe[] | undefined,
  timeMs: number
): VolumeKeyframe[] {
  if (!keyframes) return []

  // Remove keyframe within 50ms tolerance
  return keyframes.filter((kf) => Math.abs(kf.time_ms - timeMs) >= 50)
}

/**
 * Generate trapezoid fade keyframes (fade in -> sustain -> fade out)
 * @param durationMs Total clip duration in milliseconds
 * @param fadeInMs Fade in duration in milliseconds
 * @param fadeOutMs Fade out duration in milliseconds
 * @param peakVolume Peak volume during sustain (0.0 - 1.0)
 * @returns Array of keyframes forming a trapezoid envelope
 */
export function generateTrapezoidFade(
  durationMs: number,
  fadeInMs: number,
  fadeOutMs: number,
  peakVolume: number = 1.0
): VolumeKeyframe[] {
  const clampedPeak = Math.max(0, Math.min(1, peakVolume))
  const keyframes: VolumeKeyframe[] = []

  // Ensure fade durations don't exceed clip duration
  const totalFadeMs = fadeInMs + fadeOutMs
  let adjustedFadeIn = fadeInMs
  let adjustedFadeOut = fadeOutMs

  if (totalFadeMs > durationMs) {
    // Scale down proportionally
    const ratio = durationMs / totalFadeMs
    adjustedFadeIn = Math.round(fadeInMs * ratio)
    adjustedFadeOut = Math.round(fadeOutMs * ratio)
  }

  // Start at 0
  keyframes.push({ time_ms: 0, value: 0 })

  // End of fade in (peak)
  if (adjustedFadeIn > 0) {
    keyframes.push({ time_ms: adjustedFadeIn, value: clampedPeak })
  }

  // Start of fade out (peak)
  const fadeOutStart = durationMs - adjustedFadeOut
  if (adjustedFadeOut > 0 && fadeOutStart > adjustedFadeIn) {
    keyframes.push({ time_ms: fadeOutStart, value: clampedPeak })
  }

  // End at 0
  keyframes.push({ time_ms: durationMs, value: 0 })

  return keyframes
}

/**
 * Convert keyframes to SVG polyline points string
 * @param keyframes Volume keyframes
 * @param width SVG width in pixels
 * @param height SVG height in pixels
 * @param durationMs Clip duration for time-to-x conversion
 * @returns SVG points string (e.g., "0,100 50,20 100,20 150,100")
 */
export function keyframesToPolylinePoints(
  keyframes: VolumeKeyframe[] | undefined,
  width: number,
  height: number,
  durationMs: number
): string {
  if (!keyframes || keyframes.length === 0 || durationMs === 0) {
    // Default: flat line at full volume
    return `0,0 ${width},0`
  }

  const sorted = [...keyframes].sort((a, b) => a.time_ms - b.time_ms)

  return sorted
    .map((kf) => {
      const x = (kf.time_ms / durationMs) * width
      // Invert Y: 0 volume = bottom, 1 volume = top
      const y = (1 - kf.value) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
