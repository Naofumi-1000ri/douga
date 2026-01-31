import { memo, useCallback, useRef, useState } from 'react'
import type { VolumeKeyframe } from '@/store/projectStore'
import { keyframesToPolylinePoints } from '@/utils/volumeKeyframes'

interface VolumeEnvelopeProps {
  keyframes: VolumeKeyframe[] | undefined
  durationMs: number
  width: number
  height: number
  onKeyframeAdd?: (timeMs: number, value: number) => void
  onKeyframeUpdate?: (index: number, timeMs: number, value: number) => void
  onKeyframeRemove?: (index: number) => void
}

/**
 * Volume envelope visualization and editing component.
 * Displays a polyline showing volume over time with draggable keyframe points.
 */
const VolumeEnvelope = memo(function VolumeEnvelope({
  keyframes,
  durationMs,
  width,
  height,
  onKeyframeAdd,
  onKeyframeUpdate,
  onKeyframeRemove,
}: VolumeEnvelopeProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null)
  const [dragStartPos, setDragStartPos] = useState<{ x: number; y: number } | null>(null)
  const [initialKeyframe, setInitialKeyframe] = useState<VolumeKeyframe | null>(null)

  // Sort keyframes by time for rendering
  const sortedKeyframes = keyframes
    ? [...keyframes].sort((a, b) => a.time_ms - b.time_ms)
    : []

  // Convert keyframes to polyline points
  const polylinePoints = keyframesToPolylinePoints(keyframes, width, height, durationMs)

  // Handle double-click to add keyframe
  const handleDoubleClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!onKeyframeAdd || !svgRef.current) return

      const rect = svgRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      // Convert to time and value
      const timeMs = (x / width) * durationMs
      const value = 1 - y / height // Invert: top = 1, bottom = 0

      onKeyframeAdd(Math.round(timeMs), Math.max(0, Math.min(1, value)))
    },
    [onKeyframeAdd, width, height, durationMs]
  )

  // Handle keyframe drag start
  const handleKeyframeDragStart = useCallback(
    (e: React.MouseEvent, index: number) => {
      e.preventDefault()
      e.stopPropagation()

      if (!onKeyframeUpdate) return

      setDraggingIndex(index)
      setDragStartPos({ x: e.clientX, y: e.clientY })
      setInitialKeyframe(sortedKeyframes[index])
    },
    [sortedKeyframes, onKeyframeUpdate]
  )

  // Handle keyframe drag move
  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (draggingIndex === null || !dragStartPos || !initialKeyframe || !onKeyframeUpdate || !svgRef.current) {
        return
      }

      const rect = svgRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      // Convert to time and value
      const timeMs = Math.max(0, Math.min(durationMs, (x / width) * durationMs))
      const value = Math.max(0, Math.min(1, 1 - y / height))

      onKeyframeUpdate(draggingIndex, Math.round(timeMs), value)
    },
    [draggingIndex, dragStartPos, initialKeyframe, onKeyframeUpdate, width, height, durationMs]
  )

  // Handle keyframe drag end
  const handleMouseUp = useCallback(() => {
    setDraggingIndex(null)
    setDragStartPos(null)
    setInitialKeyframe(null)
  }, [])

  // Handle right-click to remove keyframe
  const handleKeyframeContextMenu = useCallback(
    (e: React.MouseEvent, index: number) => {
      e.preventDefault()
      e.stopPropagation()

      if (onKeyframeRemove) {
        onKeyframeRemove(index)
      }
    },
    [onKeyframeRemove]
  )

  // Calculate keyframe positions for rendering
  const keyframePositions = sortedKeyframes.map((kf) => ({
    x: (kf.time_ms / durationMs) * width,
    y: (1 - kf.value) * height,
    timeMs: kf.time_ms,
    value: kf.value,
  }))

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className="absolute inset-0 pointer-events-auto"
      style={{ zIndex: 15 }}
      onDoubleClick={handleDoubleClick}
      onMouseMove={draggingIndex !== null ? handleMouseMove : undefined}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Background fill under the envelope curve */}
      {sortedKeyframes.length > 0 && (
        <polygon
          points={`0,${height} ${polylinePoints} ${width},${height}`}
          fill="rgba(251, 146, 60, 0.15)"
          stroke="none"
        />
      )}

      {/* Volume envelope line */}
      {sortedKeyframes.length > 0 && (
        <polyline
          points={polylinePoints}
          fill="none"
          stroke="rgb(251, 146, 60)"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
      )}

      {/* Keyframe points */}
      {keyframePositions.map((pos, index) => (
        <g key={`kf-${index}`}>
          {/* Larger invisible hit area for easier dragging */}
          <circle
            cx={pos.x}
            cy={pos.y}
            r={12}
            fill="transparent"
            style={{ cursor: 'grab' }}
            onMouseDown={(e) => handleKeyframeDragStart(e, index)}
            onContextMenu={(e) => handleKeyframeContextMenu(e, index)}
          />
          {/* Visible keyframe point */}
          <circle
            cx={pos.x}
            cy={pos.y}
            r={4}
            fill="rgb(251, 146, 60)"
            stroke="white"
            strokeWidth={1}
            style={{ cursor: 'grab', pointerEvents: 'none' }}
          />
        </g>
      ))}
    </svg>
  )
})

export default VolumeEnvelope
