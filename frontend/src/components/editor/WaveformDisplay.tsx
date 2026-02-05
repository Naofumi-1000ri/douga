import { useEffect, useRef, memo } from 'react'

interface WaveformDisplayProps {
  peaks: number[]
  width: number
  height: number
  color: string
}

/**
 * Lightweight waveform display component using Canvas.
 * Renders pre-computed peak data without loading audio files.
 */
const WaveformDisplay = memo(function WaveformDisplay({
  peaks,
  width,
  height,
  color,
}: WaveformDisplayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || peaks.length === 0) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Set canvas size for proper resolution
    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    // Clear canvas
    ctx.clearRect(0, 0, width, height)

    // Normalize peaks: find max and scale to fill available height
    const maxPeak = Math.max(...peaks.map(p => Math.abs(p)), 0.01)
    const normalizeScale = 1 / maxPeak

    // Draw waveform
    // Calculate bar width - allow sub-pixel for zoomed out views
    const rawBarWidth = width / peaks.length
    const centerY = height / 2

    ctx.fillStyle = color

    // If bars are very small, downsample peaks for better rendering
    if (rawBarWidth < 1) {
      // Downsample: group multiple peaks together
      const samplesPerPixel = Math.ceil(1 / rawBarWidth)
      const downsampledLength = Math.ceil(peaks.length / samplesPerPixel)
      const barWidth = width / downsampledLength

      for (let i = 0; i < downsampledLength; i++) {
        // Find max peak in this group
        let maxPeakInGroup = 0
        for (let j = 0; j < samplesPerPixel && (i * samplesPerPixel + j) < peaks.length; j++) {
          maxPeakInGroup = Math.max(maxPeakInGroup, Math.abs(peaks[i * samplesPerPixel + j]))
        }
        const normalizedPeak = maxPeakInGroup * normalizeScale
        const barHeight = Math.max(1, normalizedPeak * (height - 2))
        const x = i * barWidth
        const y = centerY - barHeight / 2
        ctx.fillRect(x, y, Math.max(0.5, barWidth - 0.5), barHeight)
      }
    } else {
      // Normal rendering
      for (let i = 0; i < peaks.length; i++) {
        const normalizedPeak = Math.abs(peaks[i]) * normalizeScale
        const barHeight = Math.max(1, normalizedPeak * (height - 2))
        const x = i * rawBarWidth
        const y = centerY - barHeight / 2
        ctx.fillRect(x, y, Math.max(0.5, rawBarWidth - 0.5), barHeight)
      }
    }
  }, [peaks, width, height, color])

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        opacity: 0.6,
      }}
    />
  )
})

export default WaveformDisplay
