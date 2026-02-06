import { useEffect, useRef, memo } from 'react'
import { useWaveform } from '@/hooks/useWaveform'

interface AudioWaveformThumbnailProps {
  projectId: string
  assetId: string
  width: number
  height: number
  color?: string
  backgroundColor?: string
}

/**
 * A small waveform thumbnail for audio assets in the asset library.
 * Shows the first portion of the audio waveform.
 */
const AudioWaveformThumbnail = memo(function AudioWaveformThumbnail({
  projectId,
  assetId,
  width,
  height,
  color = '#22c55e',
  backgroundColor = 'transparent',
}: AudioWaveformThumbnailProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const { peaks, isLoading, error } = useWaveform(projectId, assetId)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !peaks || peaks.length === 0) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1

    // Set canvas size with device pixel ratio for crisp rendering
    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    ctx.scale(dpr, dpr)

    // Clear canvas
    ctx.fillStyle = backgroundColor
    ctx.fillRect(0, 0, width, height)

    // Normalize peaks for display
    const maxPeak = Math.max(...peaks.map(p => Math.abs(p)), 0.01)
    const normalizeScale = 1 / maxPeak

    // Calculate number of bars to draw based on width
    const numBars = Math.min(peaks.length, width)
    const barWidth = width / numBars
    const centerY = height / 2

    ctx.fillStyle = color

    // Draw only the first portion of waveform that fits the thumbnail
    const samplesPerBar = Math.ceil(peaks.length / numBars)

    for (let i = 0; i < numBars; i++) {
      // Find max peak in this group
      let maxPeakInGroup = 0
      for (let j = 0; j < samplesPerBar && (i * samplesPerBar + j) < peaks.length; j++) {
        maxPeakInGroup = Math.max(maxPeakInGroup, Math.abs(peaks[i * samplesPerBar + j]))
      }

      const normalizedPeak = maxPeakInGroup * normalizeScale
      const barHeight = Math.max(2, normalizedPeak * (height - 4))
      const x = i * barWidth
      const y = centerY - barHeight / 2

      ctx.fillRect(x, y, Math.max(1, barWidth - 0.5), barHeight)
    }
  }, [peaks, width, height, color, backgroundColor])

  if (isLoading) {
    return (
      <div
        className="flex items-center justify-center"
        style={{ width, height, backgroundColor }}
      >
        <div className="flex items-center gap-0.5">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-0.5 rounded-full animate-pulse"
              style={{
                height: `${20 + (i % 3) * 15}%`,
                animationDelay: `${i * 100}ms`,
                backgroundColor: color,
                opacity: 0.4,
              }}
            />
          ))}
        </div>
      </div>
    )
  }

  if (error || !peaks) {
    // Fallback to audio icon on error
    return (
      <div
        className="flex items-center justify-center"
        style={{ width, height, backgroundColor }}
      >
        <svg
          className="w-6 h-6 text-gray-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"
          />
        </svg>
      </div>
    )
  }

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
    />
  )
})

export default AudioWaveformThumbnail
