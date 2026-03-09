import type { MouseEvent as ReactMouseEvent } from 'react'
import type { PreviewDragHandle } from '@/hooks/usePreviewDragWorkflow'
import { type ActiveClipInfo, getHandleCursor } from '@/components/editor/editorPreviewStageShared'
import { ARROW_SOURCE_PATH, getArrowShapeTransform } from '@/components/editor/shapeGeometry'

interface EditorPreviewShapeClipProps {
  activeClip: ActiveClipInfo
  handlePreviewDragStart: (
    event: ReactMouseEvent,
    type: PreviewDragHandle,
    layerId: string,
    clipId: string,
  ) => void
  isDragging: boolean
  isSelected: boolean
  zIndex: number
}

export default function EditorPreviewShapeClip({
  activeClip,
  handlePreviewDragStart,
  isDragging,
  isSelected,
  zIndex,
}: EditorPreviewShapeClipProps) {
  const shape = activeClip.shape
  if (!shape) return null
  const isArrow = shape.type === 'arrow'

  return (
    <div
      className="absolute"
      style={{
        top: '50%',
        left: '50%',
        transform: `translate(-50%, -50%) translate(${activeClip.transform.x}px, ${activeClip.transform.y}px) scale(${activeClip.transform.scale}) rotate(${activeClip.transform.rotation}deg)`,
        opacity: activeClip.transform.opacity,
        zIndex,
        transformOrigin: 'center center',
      }}
    >
      <div className={`relative ${isSelected && !activeClip.locked ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-transparent' : ''}`} style={{ userSelect: 'none' }}>
        <svg width={shape.width + shape.strokeWidth} height={shape.height + shape.strokeWidth} className="block pointer-events-none">
          {shape.type === 'rectangle' && (
            <rect
              x={shape.strokeWidth / 2}
              y={shape.strokeWidth / 2}
              width={shape.width}
              height={shape.height}
              fill={shape.filled ? shape.fillColor : 'none'}
              stroke={shape.strokeColor}
              strokeWidth={shape.strokeWidth}
            />
          )}
          {shape.type === 'circle' && (
            <ellipse
              cx={(shape.width + shape.strokeWidth) / 2}
              cy={(shape.height + shape.strokeWidth) / 2}
              rx={shape.width / 2}
              ry={shape.height / 2}
              fill={shape.filled ? shape.fillColor : 'none'}
              stroke={shape.strokeColor}
              strokeWidth={shape.strokeWidth}
            />
          )}
          {shape.type === 'line' && (
            <line
              x1={shape.strokeWidth / 2}
              y1={(shape.height + shape.strokeWidth) / 2}
              x2={shape.width + shape.strokeWidth / 2}
              y2={(shape.height + shape.strokeWidth) / 2}
              stroke={shape.strokeColor}
              strokeWidth={shape.strokeWidth}
              strokeLinecap="round"
            />
          )}
          {shape.type === 'arrow' && (() => {
            const fillColor = shape.fillColor === 'transparent' ? shape.strokeColor : shape.fillColor
            return (
              <path
                data-testid="shape-arrow-path"
                d={ARROW_SOURCE_PATH}
                transform={getArrowShapeTransform(shape.width, shape.height)}
                fill={fillColor}
                stroke={shape.strokeWidth > 0 ? shape.strokeColor : 'none'}
                strokeWidth={shape.strokeWidth}
                vectorEffect="non-scaling-stroke"
              />
            )
          })()}
        </svg>
        <div
          className="absolute inset-0"
          style={{ cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab' }}
          onMouseDown={(event) => handlePreviewDragStart(event, 'move', activeClip.layerId, activeClip.clip.id)}
        />
        {isSelected && !activeClip.locked && (
          <>
            {isArrow ? (
              <>
                <div
                  data-testid="shape-arrow-start-handle"
                  className="absolute"
                  style={{ left: 0, top: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'arrow-start'), padding: 10 }}
                  onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'arrow-start', activeClip.layerId, activeClip.clip.id) }}
                >
                  <div className="w-5 h-5 rounded-full bg-primary-500 border-2 border-white pointer-events-none" />
                </div>
                <div
                  data-testid="shape-arrow-end-handle"
                  className="absolute"
                  style={{ left: shape.width, top: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'arrow-end'), padding: 10 }}
                  onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'arrow-end', activeClip.layerId, activeClip.clip.id) }}
                >
                  <div className="w-5 h-5 rounded-full bg-pink-500 border-2 border-white pointer-events-none" />
                </div>
              </>
            ) : (
              <>
                <div className="absolute" style={{ top: 0, left: 0, transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tl'), padding: 8 }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tl', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-5 bg-primary-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ top: 0, right: 0, transform: 'translate(50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-tr'), padding: 8 }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-tr', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-5 bg-primary-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ bottom: 0, left: 0, transform: 'translate(-50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-bl'), padding: 8 }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-bl', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-5 bg-primary-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ bottom: 0, right: 0, transform: 'translate(50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-br'), padding: 8 }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-br', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-5 bg-primary-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ top: 0, left: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-t'), padding: '8px 12px' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-t', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-3 bg-green-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ bottom: 0, left: '50%', transform: 'translate(-50%, 50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-b'), padding: '8px 12px' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-b', activeClip.layerId, activeClip.clip.id) }}><div className="w-5 h-3 bg-green-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ left: 0, top: '50%', transform: 'translate(-50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-l'), padding: '12px 8px' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-l', activeClip.layerId, activeClip.clip.id) }}><div className="w-3 h-5 bg-green-500 border-2 border-white rounded-sm pointer-events-none" /></div>
                <div className="absolute" style={{ right: 0, top: '50%', transform: 'translate(50%, -50%)', cursor: getHandleCursor(activeClip.transform.rotation, 'resize-r'), padding: '12px 8px' }} onMouseDown={(event) => { event.stopPropagation(); handlePreviewDragStart(event, 'resize-r', activeClip.layerId, activeClip.clip.id) }}><div className="w-3 h-5 bg-green-500 border-2 border-white rounded-sm pointer-events-none" /></div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
