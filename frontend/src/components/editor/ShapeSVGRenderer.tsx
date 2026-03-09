import { memo } from 'react'
import type { Shape } from '@/store/projectStore'
import { getArrowShapeMetrics } from '@/components/editor/shapeGeometry'

interface ShapeSVGRendererProps {
  shape: Shape
  width: number
  height: number
  opacity?: number
  className?: string
}

/**
 * Renders a shape as SVG with proper viewBox scaling.
 * Supports rectangle, circle, line, and arrow shapes.
 */
const ShapeSVGRenderer = memo(function ShapeSVGRenderer({
  shape,
  width,
  height,
  opacity = 0.7,
  className = '',
}: ShapeSVGRendererProps) {
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${shape.width} ${shape.height}`}
      style={{ opacity }}
      className={className}
    >
      {shape.type === 'rectangle' && (
        <rect
          x={shape.strokeWidth / 2}
          y={shape.strokeWidth / 2}
          width={shape.width - shape.strokeWidth}
          height={shape.height - shape.strokeWidth}
          fill={shape.filled ? shape.fillColor : 'none'}
          stroke={shape.strokeColor}
          strokeWidth={shape.strokeWidth}
        />
      )}
      {shape.type === 'circle' && (
        <ellipse
          cx={shape.width / 2}
          cy={shape.height / 2}
          rx={(shape.width - shape.strokeWidth) / 2}
          ry={(shape.height - shape.strokeWidth) / 2}
          fill={shape.filled ? shape.fillColor : 'none'}
          stroke={shape.strokeColor}
          strokeWidth={shape.strokeWidth}
        />
      )}
      {shape.type === 'line' && (
        <line
          x1={shape.strokeWidth / 2}
          y1={shape.height / 2}
          x2={shape.width - shape.strokeWidth / 2}
          y2={shape.height / 2}
          stroke={shape.strokeColor}
          strokeWidth={shape.strokeWidth}
          strokeLinecap="round"
        />
      )}
      {shape.type === 'arrow' && (() => {
        const metrics = getArrowShapeMetrics(shape)
        return (
          <>
            <line
              x1={metrics.shaftStartX}
              y1={metrics.centerY}
              x2={metrics.shaftEndX}
              y2={metrics.centerY}
              stroke={shape.strokeColor}
              strokeWidth={shape.strokeWidth}
              strokeLinecap="round"
            />
            <polygon
              points={[
                `${metrics.headBaseX},${metrics.centerY - metrics.headHalfHeight}`,
                `${metrics.headTipX},${metrics.centerY}`,
                `${metrics.headBaseX},${metrics.centerY + metrics.headHalfHeight}`,
              ].join(' ')}
              fill={shape.strokeColor}
            />
          </>
        )
      })()}
    </svg>
  )
})

export default ShapeSVGRenderer
