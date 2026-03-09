import { useTranslation } from 'react-i18next'
import { getMinimumArrowWidth } from '@/components/editor/shapeGeometry'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'

interface EditorVideoClipShapeSectionProps {
  handleUpdateShape: (updates: Record<string, unknown>) => void
  handleUpdateShapeDebounced: (updates: Record<string, unknown>) => void
  handleUpdateShapeFade: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeFadeLocal: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeLocal: (updates: Record<string, unknown>) => void
  selectedVideoClip: SelectedVideoClipInfo
}

export default function EditorVideoClipShapeSection({
  handleUpdateShape,
  handleUpdateShapeDebounced,
  handleUpdateShapeFade,
  handleUpdateShapeFadeLocal,
  handleUpdateShapeLocal,
  selectedVideoClip,
}: EditorVideoClipShapeSectionProps) {
  const { t } = useTranslation('editor')
  const shape = selectedVideoClip.shape
  const isArrow = shape?.type === 'arrow'

  if (!shape) {
    return null
  }

  return (
    <div className="pt-4 border-t border-gray-700">
      <label className="block text-xs text-gray-500 mb-3">{t('editor.shapeProps')}</label>
      <div className="space-y-3">
        {shape.type !== 'line' && shape.type !== 'arrow' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs text-gray-400">{t('editor.fill')}</label>
              <button
                onClick={() => handleUpdateShape({ filled: !shape.filled })}
                className={`px-2 py-0.5 text-xs rounded cursor-pointer transition-colors ${
                  shape.filled
                    ? 'bg-green-600 text-white hover:bg-green-700'
                    : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
                }`}
              >
                {shape.filled ? 'ON' : 'OFF'}
              </button>
            </div>
            {shape.filled && (
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-600 w-16">{t('editor.fillColor')}</label>
                <input
                  type="color"
                  value={shape.fillColor === 'transparent' ? '#000000' : shape.fillColor}
                  onChange={(e) => {
                    handleUpdateShapeLocal({ fillColor: e.target.value })
                    handleUpdateShapeDebounced({ fillColor: e.target.value })
                  }}
                  className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
                />
                <span className="text-xs text-gray-400">{shape.fillColor}</span>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-600 w-16">{t('editor.strokeColor')}</label>
          <input
            type="color"
            value={shape.strokeColor}
            onChange={(e) => {
              handleUpdateShapeLocal({ strokeColor: e.target.value })
              handleUpdateShapeDebounced({ strokeColor: e.target.value })
            }}
            className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
          />
          <span className="text-xs text-gray-400">{shape.strokeColor}</span>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-600">{t('editor.strokeWidth')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="0"
                max="20"
                step="1"
                key={`sw-${shape.strokeWidth}`}
                defaultValue={shape.strokeWidth}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    const val = Math.max(0, Math.min(20, parseInt(e.currentTarget.value) || 0))
                    handleUpdateShape({ strokeWidth: val })
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(0, Math.min(20, parseInt(e.target.value) || 0))
                  if (val !== shape.strokeWidth) {
                    handleUpdateShape({ strokeWidth: val })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">px</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="20"
            step="1"
            value={shape.strokeWidth}
            onChange={(e) => handleUpdateShapeLocal({ strokeWidth: parseInt(e.target.value) })}
            onMouseUp={(e) => handleUpdateShape({ strokeWidth: parseInt(e.currentTarget.value) })}
            onTouchEnd={(e) => handleUpdateShape({ strokeWidth: parseInt((e.target as HTMLInputElement).value) })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-xs text-gray-600">{isArrow ? t('editor.arrowLength') : t('editor.width')}</label>
            <input
              data-testid={isArrow ? 'shape-arrow-length-input' : undefined}
              type="number"
              value={shape.width}
              onChange={(e) => {
                const minimumWidth = isArrow ? Math.ceil(getMinimumArrowWidth(shape.height)) : 10
                handleUpdateShape({ width: Math.max(minimumWidth, parseInt(e.target.value) || minimumWidth) })
              }}
              className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600">{isArrow ? t('editor.arrowThickness') : t('editor.height')}</label>
            <input
              data-testid={isArrow ? 'shape-arrow-thickness-input' : undefined}
              type="number"
              value={shape.height}
              onChange={(e) => {
                const nextHeight = Math.max(10, parseInt(e.target.value) || 10)
                if (!isArrow) {
                  handleUpdateShape({ height: nextHeight })
                  return
                }
                handleUpdateShape({
                  height: nextHeight,
                  width: Math.max(shape.width, Math.ceil(getMinimumArrowWidth(nextHeight))),
                })
              }}
              className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
            />
          </div>
        </div>
        {isArrow && (
          <p className="text-xs text-gray-400">
            {t('editor.arrowThicknessHint')}
          </p>
        )}

        <div className="pt-3 border-t border-gray-600">
          <label className="block text-xs text-gray-500 mb-2">{t('editor.fadeEffect')}</label>
          <div className="space-y-2">
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-600">{t('editor.fadeIn')}</label>
                <div className="flex items-center">
                  <input
                    type="number"
                    min="0"
                    max="3000"
                    step="100"
                    key={`fi-${selectedVideoClip.fadeInMs || 0}`}
                    defaultValue={selectedVideoClip.fadeInMs || 0}
                    onKeyDown={(e) => {
                      e.stopPropagation()
                      if (e.key === 'Enter') {
                        const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                        handleUpdateShapeFade({ fadeInMs: val })
                        e.currentTarget.blur()
                      }
                    }}
                    onBlur={(e) => {
                      const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                      if (val !== (selectedVideoClip.fadeInMs || 0)) {
                        handleUpdateShapeFade({ fadeInMs: val })
                      }
                    }}
                    className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                  />
                  <span className="text-xs text-gray-500 ml-1">ms</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="3000"
                step="100"
                value={selectedVideoClip.fadeInMs || 0}
                onChange={(e) => handleUpdateShapeFadeLocal({ fadeInMs: parseInt(e.target.value) })}
                onMouseUp={(e) => handleUpdateShapeFade({ fadeInMs: parseInt(e.currentTarget.value) })}
                onTouchEnd={(e) => handleUpdateShapeFade({ fadeInMs: parseInt((e.target as HTMLInputElement).value) })}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
              />
              <div className="w-full h-1.5 bg-gray-700 rounded-lg overflow-hidden mt-1">
                <div
                  className="h-full bg-green-500 transition-all"
                  style={{ width: `${Math.min(100, ((selectedVideoClip.fadeInMs || 0) / 3000) * 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-600">{t('editor.fadeOut')}</label>
                <div className="flex items-center">
                  <input
                    type="number"
                    min="0"
                    max="3000"
                    step="100"
                    key={`fo-${selectedVideoClip.fadeOutMs || 0}`}
                    defaultValue={selectedVideoClip.fadeOutMs || 0}
                    onKeyDown={(e) => {
                      e.stopPropagation()
                      if (e.key === 'Enter') {
                        const val = Math.max(0, Math.min(3000, parseInt(e.currentTarget.value) || 0))
                        handleUpdateShapeFade({ fadeOutMs: val })
                        e.currentTarget.blur()
                      }
                    }}
                    onBlur={(e) => {
                      const val = Math.max(0, Math.min(3000, parseInt(e.target.value) || 0))
                      if (val !== (selectedVideoClip.fadeOutMs || 0)) {
                        handleUpdateShapeFade({ fadeOutMs: val })
                      }
                    }}
                    className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
                  />
                  <span className="text-xs text-gray-500 ml-1">ms</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="3000"
                step="100"
                value={selectedVideoClip.fadeOutMs || 0}
                onChange={(e) => handleUpdateShapeFadeLocal({ fadeOutMs: parseInt(e.target.value) })}
                onMouseUp={(e) => handleUpdateShapeFade({ fadeOutMs: parseInt(e.currentTarget.value) })}
                onTouchEnd={(e) => handleUpdateShapeFade({ fadeOutMs: parseInt((e.target as HTMLInputElement).value) })}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
              />
              <div className="w-full h-1.5 bg-gray-700 rounded-lg overflow-hidden mt-1">
                <div
                  className="h-full bg-red-500 transition-all"
                  style={{ width: `${Math.min(100, ((selectedVideoClip.fadeOutMs || 0) / 3000) * 100)}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
