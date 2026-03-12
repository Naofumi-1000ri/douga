export type ImageResizeHandle =
  | 'resize-tl'
  | 'resize-tr'
  | 'resize-bl'
  | 'resize-br'
  | 'resize-t'
  | 'resize-b'
  | 'resize-l'
  | 'resize-r'

export interface ComputeImageResizeRectOptions {
  dominantAxis?: 'x' | 'y'
  handleType: ImageResizeHandle
  horizontalEdge?: number
  initialHeight: number
  initialWidth: number
  initialX: number
  initialY: number
  logicalDeltaX: number
  logicalDeltaY: number
  maintainAspect: boolean
  verticalEdge?: number
}

export interface ImageResizeRect {
  height: number
  width: number
  x: number
  y: number
}

export interface ResolveImageResizeDominantAxisOptions {
  fallback?: 'x' | 'y'
  handleType: ImageResizeHandle
  initialHeight: number
  initialWidth: number
  logicalDeltaX: number
  logicalDeltaY: number
}

export function resolveImageResizeDominantAxis({
  fallback,
  handleType,
  initialHeight,
  initialWidth,
  logicalDeltaX,
  logicalDeltaY,
}: ResolveImageResizeDominantAxisOptions): 'x' | 'y' {
  if (handleType === 'resize-t' || handleType === 'resize-b') return 'y'
  if (handleType === 'resize-l' || handleType === 'resize-r') return 'x'

  const fallbackAxis = fallback ?? 'x'
  const widthChange = initialWidth > 0 ? Math.abs(logicalDeltaX) / initialWidth : 0
  const heightChange = initialHeight > 0 ? Math.abs(logicalDeltaY) / initialHeight : 0

  if (widthChange === heightChange) return fallbackAxis
  return widthChange > heightChange ? 'x' : 'y'
}

export function computeImageResizeRect({
  dominantAxis,
  handleType,
  horizontalEdge,
  initialHeight,
  initialWidth,
  initialX,
  initialY,
  logicalDeltaX,
  logicalDeltaY,
  maintainAspect,
  verticalEdge,
}: ComputeImageResizeRectOptions): ImageResizeRect {
  const aspectRatio = initialHeight > 0 ? initialWidth / initialHeight : 1
  const minimumWidth = maintainAspect ? Math.max(10, 10 * aspectRatio) : 10
  const minimumHeight = maintainAspect ? Math.max(10, 10 / aspectRatio) : 10
  const initialLeft = initialX - initialWidth / 2
  const initialRight = initialX + initialWidth / 2
  const initialTop = initialY - initialHeight / 2
  const initialBottom = initialY + initialHeight / 2

  const clampWidth = (value: number) => Math.max(maintainAspect ? minimumWidth : 10, value)
  const clampHeight = (value: number) => Math.max(maintainAspect ? minimumHeight : 10, value)

  const chooseAxis = (fallback: 'x' | 'y' = 'x'): 'x' | 'y' => {
    if (dominantAxis) return dominantAxis
    return resolveImageResizeDominantAxis({
      fallback,
      handleType,
      initialHeight,
      initialWidth,
      logicalDeltaX,
      logicalDeltaY,
    })
  }

  switch (handleType) {
    case 'resize-br': {
      const draggedRight = horizontalEdge ?? (initialRight + logicalDeltaX)
      const draggedBottom = verticalEdge ?? (initialBottom + logicalDeltaY)
      let width = clampWidth(draggedRight - initialLeft)
      let height = clampHeight(draggedBottom - initialTop)
      if (maintainAspect) {
        const axis = chooseAxis()
        if (axis === 'x') {
          width = clampWidth(draggedRight - initialLeft)
          height = width / aspectRatio
        } else {
          height = clampHeight(draggedBottom - initialTop)
          width = height * aspectRatio
        }
      }
      return { width, height, x: initialLeft + width / 2, y: initialTop + height / 2 }
    }
    case 'resize-tl': {
      const draggedLeft = horizontalEdge ?? (initialLeft + logicalDeltaX)
      const draggedTop = verticalEdge ?? (initialTop + logicalDeltaY)
      let width = clampWidth(initialRight - draggedLeft)
      let height = clampHeight(initialBottom - draggedTop)
      if (maintainAspect) {
        const axis = chooseAxis()
        if (axis === 'x') {
          width = clampWidth(initialRight - draggedLeft)
          height = width / aspectRatio
        } else {
          height = clampHeight(initialBottom - draggedTop)
          width = height * aspectRatio
        }
      }
      return { width, height, x: initialRight - width / 2, y: initialBottom - height / 2 }
    }
    case 'resize-tr': {
      const draggedRight = horizontalEdge ?? (initialRight + logicalDeltaX)
      const draggedTop = verticalEdge ?? (initialTop + logicalDeltaY)
      let width = clampWidth(draggedRight - initialLeft)
      let height = clampHeight(initialBottom - draggedTop)
      if (maintainAspect) {
        const axis = chooseAxis()
        if (axis === 'x') {
          width = clampWidth(draggedRight - initialLeft)
          height = width / aspectRatio
        } else {
          height = clampHeight(initialBottom - draggedTop)
          width = height * aspectRatio
        }
      }
      return { width, height, x: initialLeft + width / 2, y: initialBottom - height / 2 }
    }
    case 'resize-bl': {
      const draggedLeft = horizontalEdge ?? (initialLeft + logicalDeltaX)
      const draggedBottom = verticalEdge ?? (initialBottom + logicalDeltaY)
      let width = clampWidth(initialRight - draggedLeft)
      let height = clampHeight(draggedBottom - initialTop)
      if (maintainAspect) {
        const axis = chooseAxis()
        if (axis === 'x') {
          width = clampWidth(initialRight - draggedLeft)
          height = width / aspectRatio
        } else {
          height = clampHeight(draggedBottom - initialTop)
          width = height * aspectRatio
        }
      }
      return { width, height, x: initialRight - width / 2, y: initialTop + height / 2 }
    }
    case 'resize-r': {
      const width = clampWidth((horizontalEdge ?? (initialRight + logicalDeltaX)) - initialLeft)
      const height = maintainAspect ? width / aspectRatio : initialHeight
      return { width, height, x: initialLeft + width / 2, y: initialY }
    }
    case 'resize-l': {
      const width = clampWidth(initialRight - (horizontalEdge ?? (initialLeft + logicalDeltaX)))
      const height = maintainAspect ? width / aspectRatio : initialHeight
      return { width, height, x: initialRight - width / 2, y: initialY }
    }
    case 'resize-b': {
      const height = clampHeight((verticalEdge ?? (initialBottom + logicalDeltaY)) - initialTop)
      const width = maintainAspect ? height * aspectRatio : initialWidth
      return { width, height, x: initialX, y: initialTop + height / 2 }
    }
    case 'resize-t': {
      const height = clampHeight(initialBottom - (verticalEdge ?? (initialTop + logicalDeltaY)))
      const width = maintainAspect ? height * aspectRatio : initialWidth
      return { width, height, x: initialX, y: initialBottom - height / 2 }
    }
  }
}
