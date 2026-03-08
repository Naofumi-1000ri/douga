import type { WaveformData } from '@/api/assets'
import type { AudioClip as TimelineAudioClip } from '@/store/projectStore'

const DEFAULT_TARGET_PEAK = 0.9

export function getVisibleWaveformPeaks(
  peaks: number[] | null | undefined,
  waveformDurationMs: number | null | undefined,
  inPointMs: number,
  sourceDurationMs: number,
  assetDurationMs: number | null | undefined
): number[] {
  const sourceAssetDurationMs = waveformDurationMs && waveformDurationMs > 0
    ? waveformDurationMs
    : assetDurationMs

  if (!peaks || peaks.length === 0 || !sourceAssetDurationMs || sourceAssetDurationMs <= 0) {
    return peaks ?? []
  }

  const sourceEndMs = Math.min(inPointMs + Math.max(sourceDurationMs, 1), sourceAssetDurationMs)
  const startRatio = Math.max(0, Math.min(inPointMs / sourceAssetDurationMs, 1))
  const endRatio = Math.max(startRatio, Math.min(sourceEndMs / sourceAssetDurationMs, 1))
  const startIdx = Math.min(Math.floor(startRatio * peaks.length), Math.max(peaks.length - 1, 0))
  const endIdx = Math.max(startIdx + 1, Math.min(Math.ceil(endRatio * peaks.length), peaks.length))

  return peaks.slice(startIdx, endIdx)
}

export function getMaxPeak(peaks: number[] | null | undefined): number {
  if (!peaks || peaks.length === 0) return 0
  return Math.max(...peaks.map((peak) => Math.abs(peak)), 0)
}

export function getClipMaxGain(clip: TimelineAudioClip): number {
  const keyframeMax = clip.volume_keyframes && clip.volume_keyframes.length > 0
    ? Math.max(...clip.volume_keyframes.map((keyframe) => keyframe.value), 0)
    : 0

  return Math.max(clip.volume, keyframeMax, 0)
}

export function getNormalizationScaleFactor(
  visiblePeak: number,
  currentGain: number,
  targetPeak: number = DEFAULT_TARGET_PEAK
): number {
  if (visiblePeak <= 0 || currentGain <= 0) return 1

  const desiredScale = targetPeak / (visiblePeak * currentGain)
  const maxAllowedScale = 1 / currentGain

  return Math.max(0, Math.min(desiredScale, maxAllowedScale))
}

export function scaleAudioClipGain(clip: TimelineAudioClip, scaleFactor: number): TimelineAudioClip {
  if (!Number.isFinite(scaleFactor) || scaleFactor <= 0 || Math.abs(scaleFactor - 1) < 0.0001) {
    return clip
  }

  return {
    ...clip,
    volume: Math.max(0, Math.min(1, clip.volume * scaleFactor)),
    volume_keyframes: clip.volume_keyframes?.map((keyframe) => ({
      ...keyframe,
      value: Math.max(0, Math.min(1, keyframe.value * scaleFactor)),
    })),
  }
}

export function getClipVisiblePeak(
  clip: TimelineAudioClip,
  waveform: WaveformData,
  assetDurationMs: number | null | undefined
): number {
  const clipSpeed = clip.speed || 1
  const sourceDurationMs = clip.duration_ms * clipSpeed
  const visiblePeaks = getVisibleWaveformPeaks(
    waveform.peaks,
    waveform.duration_ms,
    clip.in_point_ms,
    sourceDurationMs,
    assetDurationMs
  )

  return getMaxPeak(visiblePeaks)
}
