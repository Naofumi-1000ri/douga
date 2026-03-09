export const ARROW_VIEWBOX_WIDTH = 300
export const ARROW_VIEWBOX_HEIGHT = 80
export const ARROW_PATH_WIDTH = 230
export const ARROW_PATH_HEIGHT = 40
export const ARROW_PATH_MIN_Y = 20

// The user-provided SVG is the source of truth for the arrow silhouette.
export const ARROW_SOURCE_PATH = 'M0 40 L160 34 L154 20 L230 40 L154 60 L160 46 Z'

export function getArrowShapeTransform(width: number, height: number): string {
  return `translate(0 ${-(ARROW_PATH_MIN_Y * height) / ARROW_PATH_HEIGHT}) scale(${width / ARROW_PATH_WIDTH} ${height / ARROW_PATH_HEIGHT})`
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
  rotationDegrees: number,
) {
  const halfLength = width / 2
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
