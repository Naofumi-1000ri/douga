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

    // Draw waveform
    const barWidth = Math.max(1, width / peaks.length)
    const centerY = height / 2

    ctx.fillStyle = color

    for (let i = 0; i < peaks.length; i++) {
      const peak = Math.abs(peaks[i])
      const barHeight = Math.max(1, peak * (height - 2))
      const x = i * barWidth
      const y = centerY - barHeight / 2

      ctx.fillRect(x, y, Math.max(1, barWidth - 0.5), barHeight)
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
