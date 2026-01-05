import type { Keyframe, Clip } from '@/store/projectStore'

interface InterpolatedTransform {
  x: number
  y: number
  scale: number
  rotation: number
  opacity: number
}

/**
 * Linear interpolation between two values
 */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * Get interpolated transform at a specific time within a clip
 * @param clip The clip containing keyframes
 * @param timeInClipMs Time relative to clip start (0 = clip start)
 * @returns Interpolated transform values
 */
export function getInterpolatedTransform(
  clip: Clip,
  timeInClipMs: number
): InterpolatedTransform {
  const keyframes = clip.keyframes

  // Default transform from clip
  const defaultTransform: InterpolatedTransform = {
    x: clip.transform.x,
    y: clip.transform.y,
    scale: clip.transform.scale,
    rotation: clip.transform.rotation,
    opacity: clip.effects.opacity,
  }

  // No keyframes - return clip's base transform
  if (!keyframes || keyframes.length === 0) {
    return defaultTransform
  }

  // Sort keyframes by time
  const sortedKeyframes = [...keyframes].sort((a, b) => a.time_ms - b.time_ms)

  // Before first keyframe - use first keyframe values
  if (timeInClipMs <= sortedKeyframes[0].time_ms) {
    const kf = sortedKeyframes[0]
    return {
      x: kf.transform.x,
      y: kf.transform.y,
      scale: kf.transform.scale,
      rotation: kf.transform.rotation,
      opacity: kf.opacity ?? clip.effects.opacity,
    }
  }

  // After last keyframe - use last keyframe values
  if (timeInClipMs >= sortedKeyframes[sortedKeyframes.length - 1].time_ms) {
    const kf = sortedKeyframes[sortedKeyframes.length - 1]
    return {
      x: kf.transform.x,
      y: kf.transform.y,
      scale: kf.transform.scale,
      rotation: kf.transform.rotation,
      opacity: kf.opacity ?? clip.effects.opacity,
    }
  }

  // Find surrounding keyframes
  let prevKeyframe: Keyframe | null = null
  let nextKeyframe: Keyframe | null = null

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

  if (!prevKeyframe || !nextKeyframe) {
    return defaultTransform
  }

  // Calculate interpolation factor (0-1)
  const duration = nextKeyframe.time_ms - prevKeyframe.time_ms
  const elapsed = timeInClipMs - prevKeyframe.time_ms
  const t = duration > 0 ? elapsed / duration : 0

  // Linear interpolation
  return {
    x: lerp(prevKeyframe.transform.x, nextKeyframe.transform.x, t),
    y: lerp(prevKeyframe.transform.y, nextKeyframe.transform.y, t),
    scale: lerp(prevKeyframe.transform.scale, nextKeyframe.transform.scale, t),
    rotation: lerp(prevKeyframe.transform.rotation, nextKeyframe.transform.rotation, t),
    opacity: lerp(
      prevKeyframe.opacity ?? clip.effects.opacity,
      nextKeyframe.opacity ?? clip.effects.opacity,
      t
    ),
  }
}

/**
 * Add or update a keyframe at the specified time
 */
export function addKeyframe(
  clip: Clip,
  timeInClipMs: number,
  transform: { x: number; y: number; scale: number; rotation: number },
  opacity?: number
): Keyframe[] {
  const keyframes = clip.keyframes ? [...clip.keyframes] : []

  // Check if keyframe exists at this time (within 100ms tolerance)
  const existingIndex = keyframes.findIndex(
    (kf) => Math.abs(kf.time_ms - timeInClipMs) < 100
  )

  const newKeyframe: Keyframe = {
    time_ms: timeInClipMs,
    transform,
    opacity,
  }

  if (existingIndex >= 0) {
    // Update existing keyframe
    keyframes[existingIndex] = newKeyframe
  } else {
    // Add new keyframe
    keyframes.push(newKeyframe)
  }

  // Sort by time
  return keyframes.sort((a, b) => a.time_ms - b.time_ms)
}

/**
 * Remove a keyframe at the specified time
 */
export function removeKeyframe(clip: Clip, timeInClipMs: number): Keyframe[] {
  if (!clip.keyframes) return []

  // Remove keyframe within 100ms tolerance
  return clip.keyframes.filter(
    (kf) => Math.abs(kf.time_ms - timeInClipMs) >= 100
  )
}

/**
 * Check if a keyframe exists at the specified time
 */
export function hasKeyframeAt(clip: Clip, timeInClipMs: number): boolean {
  if (!clip.keyframes) return false
  return clip.keyframes.some((kf) => Math.abs(kf.time_ms - timeInClipMs) < 100)
}
