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

  // Calculate the full waveform width and offset for "shave off" effect
  const { fullWidth, offsetX } = useMemo(() => {
    if (!assetDurationMs || assetDurationMs <= 0 || clipDurationMs <= 0) {
      return { fullWidth: width, offsetX: 0 }
    }
    // Full waveform width based on asset duration relative to visible clip
    const fullW = (assetDurationMs / clipDurationMs) * width
    // Offset based on in-point
    const offX = (inPointMs / assetDurationMs) * fullW
    return { fullWidth: fullW, offsetX: offX }
  }, [width, assetDurationMs, clipDurationMs, inPointMs])

  // Show placeholder while loading waveform
  if (!fullPeaks) {
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
      {/* Render full waveform, offset to show only the visible portion (shave off effect) */}
      <div style={{ marginLeft: -offsetX }}>
        <WaveformDisplay
          peaks={fullPeaks}
          width={fullWidth}
          height={height}
          color={color}
        />
      </div>
    </div>
  )
})

export default AudioClipWaveform
