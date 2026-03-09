export const ARROW_REFERENCE_HEIGHT = 40
export const ARROW_REFERENCE_HEAD_LENGTH = 76
export const ARROW_REFERENCE_SHAFT_JOIN_OFFSET = 70
export const ARROW_REFERENCE_SHAFT_HALF_THICKNESS = 6
export const ARROW_REFERENCE_MIN_SHAFT_LENGTH = 24

function getArrowHeightScale(height: number) {
  return height / ARROW_REFERENCE_HEIGHT
}

export function getArrowHeadLength(height: number) {
  return ARROW_REFERENCE_HEAD_LENGTH * getArrowHeightScale(height)
}

export function getMinimumArrowWidth(height: number) {
  return getArrowHeadLength(height) + ARROW_REFERENCE_MIN_SHAFT_LENGTH * getArrowHeightScale(height)
}

export function getArrowShapePath(width: number, height: number): string {
  const safeHeight = Math.max(1, height)
  const scale = getArrowHeightScale(safeHeight)
  const safeWidth = Math.max(getMinimumArrowWidth(safeHeight), width)
  const centerY = safeHeight / 2
  const headBaseX = safeWidth - getArrowHeadLength(safeHeight)
  const shaftJoinX = safeWidth - ARROW_REFERENCE_SHAFT_JOIN_OFFSET * scale
  const shaftHalfThickness = ARROW_REFERENCE_SHAFT_HALF_THICKNESS * scale

  const points: Array<[number, number]> = [
    [0, centerY],
    [shaftJoinX, centerY - shaftHalfThickness],
    [headBaseX, 0],
    [safeWidth, centerY],
    [headBaseX, safeHeight],
    [shaftJoinX, centerY + shaftHalfThickness],
  ]

  return `${points
    .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(' ')} Z`
}

function rotateOffset(x: number, y: number, rotationDegrees: number) {
  const radians = (rotationDegrees * Math.PI) / 180
  const cos = Math.cos(radians)
  const sin = Math.sin(radians)
  return {
    x: x * cos - y * sin,
    y: x * sin + y * cos,
  }
}

export function getArrowEndpointPositions(
  centerX: number,
  centerY: number,
  width: number,
  height: number,
  rotationDegrees: number,
) {
  const halfLength = Math.max(getMinimumArrowWidth(height), width) / 2
  const startOffset = rotateOffset(-halfLength, 0, rotationDegrees)
  const endOffset = rotateOffset(halfLength, 0, rotationDegrees)
  return {
    start: {
      x: centerX + startOffset.x,
      y: centerY + startOffset.y,
    },
    end: {
      x: centerX + endOffset.x,
      y: centerY + endOffset.y,
    },
  }
}
