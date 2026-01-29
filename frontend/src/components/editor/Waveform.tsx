import { useEffect, useRef, useCallback } from 'react'
import { useWaveform } from '@/hooks/useWaveform'

interface WaveformProps {
  projectId: string
  assetId: string
  width: number
  height: number
  color?: string
  backgroundColor?: string
  className?: string
}

export default function Waveform({
  projectId,
  assetId,
  width,
  height,
  color = '#22c55e',
  backgroundColor = 'transparent',
  className = '',
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  // Use shared waveform hook with caching
  const { peaks, isLoading: loading, error } = useWaveform(projectId, assetId, width)

  // Draw waveform on canvas
  const drawWaveform = useCallback(() => {
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

    // Draw waveform
    const barWidth = width / peaks.length
    const centerY = height / 2

    ctx.fillStyle = color

    peaks.forEach((peak, i) => {
      const barHeight = Math.max(2, peak * height * 0.9) // 90% of height max
      const x = i * barWidth
      const y = centerY - barHeight / 2

      // Draw bar centered vertically
      ctx.fillRect(x, y, Math.max(barWidth - 1, 1), barHeight)
    })
  }, [peaks, width, height, color, backgroundColor])

  useEffect(() => {
    drawWaveform()
  }, [drawWaveform])

  if (loading) {
    return (
      <div
        className={`flex items-center justify-center ${className}`}
        style={{ width, height, backgroundColor }}
      >
        <div className="animate-pulse bg-gray-600 rounded w-full h-1/2" />
      </div>
    )
  }

  if (error) {
    return (
      <div
        className={`flex items-center justify-center text-gray-500 text-xs ${className}`}
        style={{ width, height, backgroundColor }}
      >
        <span title={error}>Error</span>
      </div>
    )
  }

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width, height }}
    />
  )
}

// Mini waveform for clip display in timeline
export function MiniWaveform({
  projectId,
  assetId,
  className = '',
}: {
  projectId: string
  assetId: string
  className?: string
}) {
  return (
    <Waveform
      projectId={projectId}
      assetId={assetId}
      width={200}
      height={40}
      color="#22c55e88"
      backgroundColor="transparent"
      className={className}
    />
  )
}
