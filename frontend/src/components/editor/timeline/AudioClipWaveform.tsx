import { memo, useMemo } from 'react'

import { useWaveform } from '@/hooks/useWaveform'

import WaveformDisplay from '../WaveformDisplay'

interface AudioClipWaveformProps {
  projectId: string
  assetId: string
  width: number
  height: number
  color: string
  inPointMs: number      // Where in the source the clip starts
  clipDurationMs: number // Duration of the clip on timeline
  assetDurationMs: number // Total duration of the source asset
}

const AudioClipWaveform = memo(function AudioClipWaveform({
  projectId,
  assetId,
  width,
  height,
  color,
  inPointMs,
  clipDurationMs,
  assetDurationMs,
}: AudioClipWaveformProps) {
  // Calculate samples based on clip width (1 sample per 2 pixels)
  const samples = Math.max(50, Math.min(400, Math.floor(width / 2)))
  const { peaks: fullPeaks, isLoading } = useWaveform(projectId, assetId, samples)

  // Slice peaks array to only show the trimmed portion
  const peaks = useMemo(() => {
    if (!fullPeaks || fullPeaks.length === 0 || assetDurationMs <= 0) return fullPeaks

    // Calculate which portion of the peaks array represents the visible clip
    const startRatio = inPointMs / assetDurationMs
    const endRatio = (inPointMs + clipDurationMs) / assetDurationMs

    const startIdx = Math.floor(startRatio * fullPeaks.length)
    const endIdx = Math.ceil(endRatio * fullPeaks.length)

    // Clamp indices to valid range
    const clampedStart = Math.max(0, Math.min(startIdx, fullPeaks.length - 1))
    const clampedEnd = Math.max(clampedStart + 1, Math.min(endIdx, fullPeaks.length))

    return fullPeaks.slice(clampedStart, clampedEnd)
  }, [fullPeaks, inPointMs, clipDurationMs, assetDurationMs])

  // Show placeholder while loading waveform (doesn't block playback)
  if (!peaks) {
    return (
      <div className="absolute inset-0 overflow-hidden pointer-events-none flex items-center justify-center">
        {isLoading ? (
          <div className="flex items-center gap-1">
            {[...Array(5)].map((_, i) => (
              <div
                key={i}
                className="w-1 bg-current opacity-40 rounded-full animate-pulse"
                style={{
                  height: `${20 + (i % 3) * 10}%`,
                  animationDelay: `${i * 100}ms`,
                  color,
                }}
              />
            ))}
          </div>
        ) : (
          <div className="w-full h-px opacity-30" style={{ backgroundColor: color }} />
        )}
      </div>
    )
  }

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <WaveformDisplay peaks={peaks} width={width} height={height} color={color} />
    </div>
  )
})

export default AudioClipWaveform
