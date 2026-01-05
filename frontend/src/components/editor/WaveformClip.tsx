import { useEffect, useRef, memo } from 'react'
import WaveSurfer from 'wavesurfer.js'

interface WaveformClipProps {
  audioUrl: string
  clipId: string
  startMs: number
  durationMs: number
  pixelsPerSecond: number
  color?: string
  onSelect?: () => void
  selected?: boolean
}

const WaveformClip = memo(function WaveformClip({
  audioUrl,
  clipId: _clipId,
  startMs,
  durationMs,
  pixelsPerSecond,
  color = '#22c55e',
  onSelect,
  selected = false,
}: WaveformClipProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wavesurferRef = useRef<WaveSurfer | null>(null)

  useEffect(() => {
    if (!containerRef.current || !audioUrl) return

    const wavesurfer = WaveSurfer.create({
      container: containerRef.current,
      waveColor: color,
      progressColor: color,
      cursorWidth: 0,
      height: 48,
      normalize: true,
      interact: false,
      fillParent: true,
      minPxPerSec: pixelsPerSecond,
    })

    wavesurfer.load(audioUrl)
    wavesurferRef.current = wavesurfer

    return () => {
      wavesurfer.destroy()
    }
  }, [audioUrl, color, pixelsPerSecond])

  const width = (durationMs / 1000) * pixelsPerSecond
  const left = (startMs / 1000) * pixelsPerSecond

  return (
    <div
      className={`absolute top-1 bottom-1 rounded cursor-pointer transition-all ${
        selected ? 'ring-2 ring-white ring-offset-1 ring-offset-gray-900' : ''
      }`}
      style={{
        left,
        width,
        backgroundColor: `${color}33`,
        borderColor: color,
        borderWidth: 1,
      }}
      onClick={onSelect}
    >
      <div ref={containerRef} className="w-full h-full overflow-hidden" />
    </div>
  )
})

export default WaveformClip
