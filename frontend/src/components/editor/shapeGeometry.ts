import type { Shape } from '@/store/projectStore'

export function getArrowShapePoints(shape: Shape): string {
  const inset = 1
  const centerY = shape.height / 2
  const tipX = shape.width - inset
  const headLength = Math.min(
    shape.width * 0.34,
    Math.max(shape.width * 0.2, shape.height * 1.05, shape.strokeWidth * 4)
  )
  const headBaseX = Math.max(inset + 18, tipX - headLength)
  const shoulderX = Math.max(inset + 10, headBaseX - Math.max(shape.width * 0.16, 12))
  const tailHalfHeight = Math.min(
    shape.height * 0.18,
    Math.max(shape.strokeWidth * 0.18, shape.height * 0.05, 2)
  )
  const bodyHalfHeight = Math.min(
    shape.height * 0.23,
    Math.max(tailHalfHeight + 2, shape.height * 0.12, shape.strokeWidth * 0.22)
  )
  const headHalfHeight = Math.min(
    shape.height / 2 - inset,
    Math.max(bodyHalfHeight + 5, shape.height * 0.36, shape.strokeWidth * 0.7)
  )

  return [
    `${inset},${centerY - tailHalfHeight}`,
    `${shoulderX},${centerY - bodyHalfHeight}`,
    `${headBaseX},${centerY - bodyHalfHeight}`,
    `${tipX},${centerY}`,
    `${headBaseX},${centerY + headHalfHeight}`,
    `${shoulderX},${centerY + bodyHalfHeight}`,
    `${inset},${centerY + tailHalfHeight}`,
  ].join(' ')
}
