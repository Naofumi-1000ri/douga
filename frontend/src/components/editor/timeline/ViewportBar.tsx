import React from 'react'

interface ScrollPosition {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

interface ViewportBarProps {
  headerWidth: number
  scrollPosition: ScrollPosition
  zoom: number
  timelineDurationMs: number
  viewportBarDrag: {
    type: 'left' | 'right' | 'move'
    startX: number
    initialZoom: number
    initialScrollLeft: number
    initialBarLeft: number
    initialBarRight: number
    initialRightTimeMs: number
    initialLeftTimeMs: number
  } | null
  onViewportBarDragStart: (e: React.MouseEvent<HTMLDivElement>, type: 'left' | 'right' | 'move') => void
  viewportBarRef: React.RefObject<HTMLDivElement>
}

function ViewportBar({
  headerWidth,
  scrollPosition,
  zoom,
  timelineDurationMs,
  viewportBarDrag,
  onViewportBarDragStart,
  viewportBarRef,
}: ViewportBarProps) {
  const clientW = scrollPosition.clientWidth || 800
  const pixelsPerSecond = 10 * zoom

  // Content width - for bar width/zoom calculation
  const clipW = (timelineDurationMs / 1000) * pixelsPerSecond
  const minW = 120 * pixelsPerSecond
  const contentW = Math.max(clipW, minW)

  // Total canvas width (with right padding) - for scroll position calculation
  const totalCanvasW = contentW + clientW

  // Bar width = visible portion of TOTAL canvas (padding + content)
  const barWidthPercent = Math.min(100, (clientW / totalCanvasW) * 100)

  // Bar position = scroll position mapped to bar container (using total scrollable range)
  const scrollableWidth = totalCanvasW - clientW
  const barLeftPercent = scrollableWidth > 0
    ? (scrollPosition.scrollLeft / scrollableWidth) * (100 - barWidthPercent)
    : 0

  return (
    <div
      ref={viewportBarRef}
      className="h-5 mt-1 bg-gray-900 rounded relative border border-gray-700"
      style={{ marginLeft: headerWidth, marginRight: 16 }}
    >
      <div
        className={`absolute top-0 bottom-0 bg-gray-600 rounded cursor-grab active:cursor-grabbing ${
          viewportBarDrag ? 'bg-gray-500' : 'hover:bg-gray-500'
        }`}
        style={{
          left: `${barLeftPercent}%`,
          width: `${barWidthPercent}%`,
          minWidth: 30,
        }}
        onMouseDown={(e) => onViewportBarDragStart(e, 'move')}
      >
        <div
          className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-primary-500/50 rounded-l flex items-center justify-center"
          onMouseDown={(e) => {
            e.stopPropagation()
            onViewportBarDragStart(e, 'left')
          }}
          title="左にドラッグ=ズームアウト、右=ズームイン"
        >
          <div className="w-0.5 h-3 bg-gray-400 rounded" />
        </div>
        <div
          className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-primary-500/50 rounded-r flex items-center justify-center"
          onMouseDown={(e) => {
            e.stopPropagation()
            onViewportBarDragStart(e, 'right')
          }}
          title="右にドラッグ=ズームアウト、左=ズームイン"
        >
          <div className="w-0.5 h-3 bg-gray-400 rounded" />
        </div>
      </div>
    </div>
  )
}

export default ViewportBar
