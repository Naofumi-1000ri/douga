import type { MouseEvent as ReactMouseEvent } from 'react'
import type { PreviewDragHandle } from '@/hooks/usePreviewDragWorkflow'
import { DEFAULT_TEXT_STYLE, type ActiveClipInfo, getTextBackgroundColor } from '@/components/editor/editorPreviewStageShared'

interface EditorPreviewTextClipProps {
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

export default function EditorPreviewTextClip({
  activeClip,
  handlePreviewDragStart,
  isDragging,
  isSelected,
  zIndex,
}: EditorPreviewTextClipProps) {
  if (activeClip.clip.text_content === undefined) return null

  const textStyle = activeClip.clip.text_style || DEFAULT_TEXT_STYLE

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
      <div
        className={`relative ${isSelected && !activeClip.locked ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-transparent' : ''}`}
        style={{
          cursor: activeClip.locked ? 'not-allowed' : isDragging ? 'grabbing' : 'grab',
          userSelect: 'none',
          backgroundColor: getTextBackgroundColor(textStyle.backgroundColor, textStyle.backgroundOpacity ?? 1),
          padding: textStyle.backgroundColor !== 'transparent' && (textStyle.backgroundOpacity ?? 1) > 0 ? '8px 16px' : '0',
          borderRadius: textStyle.backgroundColor !== 'transparent' && (textStyle.backgroundOpacity ?? 1) > 0 ? '4px' : '0',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: textStyle.verticalAlign === 'top' ? 'flex-start' : textStyle.verticalAlign === 'bottom' ? 'flex-end' : 'center',
          textAlign: textStyle.textAlign,
          minWidth: '50px',
        }}
        onMouseDown={(event) => handlePreviewDragStart(event, 'move', activeClip.layerId, activeClip.clip.id)}
      >
        <span
          style={{
            fontFamily: textStyle.fontFamily,
            fontSize: `${textStyle.fontSize}px`,
            fontWeight: textStyle.fontWeight,
            fontStyle: textStyle.fontStyle,
            color: textStyle.color,
            lineHeight: textStyle.lineHeight,
            letterSpacing: `${textStyle.letterSpacing}px`,
            WebkitTextStroke: textStyle.strokeWidth > 0 ? `${textStyle.strokeWidth}px ${textStyle.strokeColor}` : 'none',
            paintOrder: 'stroke fill',
            whiteSpace: 'pre',
            display: 'block',
          }}
        >
          {activeClip.clip.text_content}
        </span>
      </div>
    </div>
  )
}
