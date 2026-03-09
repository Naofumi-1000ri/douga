export const ARROW_REFERENCE_HEIGHT = 80
export const ARROW_REFERENCE_WIDTH = 230
export const ARROW_REFERENCE_HEAD_LENGTH = 76
const ARROW_REFERENCE_POINTS: Array<[number, number]> = [
  [0, 40],
  [160, 34],
  [154, 20],
  [230, 40],
  [154, 60],
  [160, 46],
]

function getArrowScale(height: number) {
  return height / ARROW_REFERENCE_HEIGHT
}

export function getArrowHeadLength(height: number) {
  return ARROW_REFERENCE_HEAD_LENGTH * getArrowScale(height)
}

export function getMinimumArrowWidth(height: number) {
  return ARROW_REFERENCE_WIDTH * getArrowScale(height)
}

export function getArrowShapePath(width: number, height: number): string {
  const safeHeight = Math.max(1, height)
  const scale = getArrowScale(safeHeight)
  const safeWidth = Math.max(getMinimumArrowWidth(safeHeight), width)
  const unscaledWidth = safeWidth / scale
  const extraShaftLength = Math.max(0, unscaledWidth - ARROW_REFERENCE_WIDTH)
  const points = ARROW_REFERENCE_POINTS.map(([x, y], index) => {
    const adjustedX = index === 0 ? x : x + extraShaftLength
    return [adjustedX * scale, y * scale] as const
  })

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
