import type { Shape } from '@/store/projectStore'

export interface ArrowShapeMetrics {
  centerY: number
  headBaseX: number
  headHalfHeight: number
  headTipX: number
  shaftEndX: number
  shaftStartX: number
}

export function getArrowShapeMetrics(shape: Shape): ArrowShapeMetrics {
  const minInset = Math.max(shape.strokeWidth / 2, 2)
  const centerY = shape.height / 2
  const headLength = Math.min(
    shape.width * 0.42,
    Math.max(shape.width * 0.22, shape.height * 0.78, shape.strokeWidth * 3)
  )
  const headTipX = shape.width - minInset
  const headBaseX = Math.max(minInset + 8, headTipX - headLength)
  const headHalfHeight = Math.min(
    shape.height / 2 - minInset,
    Math.max(shape.height * 0.26, shape.strokeWidth * 1.45)
  )
  const shaftStartX = minInset
  const shaftEndX = Math.max(shaftStartX, headBaseX - Math.max(shape.strokeWidth * 0.35, 4))

  return {
    centerY,
    headBaseX,
    headHalfHeight,
    headTipX,
    shaftEndX,
    shaftStartX,
  }
}
