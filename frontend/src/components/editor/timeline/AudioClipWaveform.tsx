import { memo, useMemo } from 'react'

import { useWaveform } from '@/hooks/useWaveform'
import { RequestPriority } from '@/utils/requestPriority'

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
  // Request waveform data (cached, uses samples_per_second for consistent quality)
  // MEDIUM priority: Timeline waveforms load after thumbnails but before asset library content
  const { peaks: fullPeaks, isLoading } = useWaveform(projectId, assetId, RequestPriority.MEDIUM)

  // Slice peaks to show only the visible portion based on in-point and clip duration
  const visiblePeaks = useMemo(() => {
    if (!fullPeaks || fullPeaks.length === 0 || !assetDurationMs || assetDurationMs <= 0) {
      return fullPeaks
    }
    const startRatio = inPointMs / assetDurationMs
    const endRatio = Math.min((inPointMs + clipDurationMs) / assetDurationMs, 1)
    const startIdx = Math.floor(startRatio * fullPeaks.length)
    const endIdx = Math.ceil(endRatio * fullPeaks.length)
    return fullPeaks.slice(startIdx, endIdx)
  }, [fullPeaks, inPointMs, clipDurationMs, assetDurationMs])

  // Show placeholder while loading waveform
  if (!visiblePeaks || visiblePeaks.length === 0) {
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
      <WaveformDisplay
        peaks={visiblePeaks}
        width={width}
        height={height}
        color={color}
      />
    </div>
  )
})

export default AudioClipWaveform
