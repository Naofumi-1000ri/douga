export const ARROW_VIEWBOX_WIDTH = 300
export const ARROW_VIEWBOX_HEIGHT = 80

// The user-provided SVG is the source of truth for the arrow silhouette.
export const ARROW_SOURCE_PATH = 'M0 40 L160 34 L154 20 L230 40 L154 60 L160 46 Z'

export function getArrowShapeTransform(width: number, height: number): string {
  return `scale(${width / ARROW_VIEWBOX_WIDTH} ${height / ARROW_VIEWBOX_HEIGHT})`
}
