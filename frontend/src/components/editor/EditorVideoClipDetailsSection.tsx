import { useTranslation } from 'react-i18next'
import type { Asset } from '@/api/assets'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'

type UpdateHandler = (updates: Record<string, unknown>) => void

interface EditorVideoClipDetailsSectionProps {
  clipAsset?: Asset
  handleUpdateVideoClip: UpdateHandler
  handleUpdateVideoClipDebounced: UpdateHandler
  handleUpdateVideoClipLocal: UpdateHandler
  handleUpdateVideoClipTiming: (updates: { startMs?: number; durationMs?: number }) => void
  selectedVideoClip: SelectedVideoClipInfo
}

export default function EditorVideoClipDetailsSection({
  clipAsset,
  handleUpdateVideoClip,
  handleUpdateVideoClipDebounced,
  handleUpdateVideoClipLocal,
  handleUpdateVideoClipTiming,
  selectedVideoClip,
}: EditorVideoClipDetailsSectionProps) {
  const { t } = useTranslation('editor')
  const isVideoAsset = clipAsset?.type === 'video'
  const showSpeedControl = (() => {
    if (selectedVideoClip.textContent) return false
    if (selectedVideoClip.shape) return false
    if (clipAsset?.type === 'image') return false
    return true
  })()

  return (
    <>
      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.clipName')}</label>
        <p className="text-white text-sm truncate">{selectedVideoClip.assetName}</p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.layer')}</label>
        <span className="inline-block px-2 py-0.5 text-xs rounded bg-gray-600 text-white">
          {selectedVideoClip.layerName}
        </span>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.startPosition')}</label>
        <input
          type="text"
          inputMode="decimal"
          defaultValue={(selectedVideoClip.startMs / 1000).toFixed(2)}
          key={`start-${selectedVideoClip.clipId}-${selectedVideoClip.startMs}`}
          onBlur={(e) => {
            const val = parseFloat(e.target.value)
            if (!Number.isNaN(val) && val >= 0) {
              handleUpdateVideoClipTiming({ startMs: Math.round(val * 1000) })
            } else {
              e.target.value = (selectedVideoClip.startMs / 1000).toFixed(2)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.currentTarget.blur()
            }
          }}
          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
        />
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('editor.duration')}</label>
        <input
          type="text"
          inputMode="decimal"
          defaultValue={(selectedVideoClip.durationMs / 1000).toFixed(2)}
          key={`duration-${selectedVideoClip.clipId}-${selectedVideoClip.durationMs}`}
          onBlur={(e) => {
            const val = parseFloat(e.target.value)
            if (!Number.isNaN(val) && val >= 0.1) {
              handleUpdateVideoClipTiming({ durationMs: Math.round(val * 1000) })
            } else {
              e.target.value = (selectedVideoClip.durationMs / 1000).toFixed(2)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.currentTarget.blur()
            }
          }}
          className="w-full bg-gray-700 text-white text-sm px-2 py-1 rounded"
        />
      </div>

      {selectedVideoClip.assetId && (
        <div className="pt-4 border-t border-gray-700">
          <label className="block text-xs text-gray-500 mb-2">{t('editor.sourceInfo')}</label>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-gray-400 mb-1">{t('editor.cutStart')}</label>
              <p className="text-white text-sm bg-gray-700 px-2 py-1 rounded">
                {(selectedVideoClip.inPointMs / 1000).toFixed(2)}s
              </p>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">{t('editor.cutLength')}</label>
              <p className="text-white text-sm bg-gray-700 px-2 py-1 rounded">
                {((selectedVideoClip.outPointMs - selectedVideoClip.inPointMs) / 1000).toFixed(2)}s
              </p>
            </div>
          </div>
          <p className="text-xs text-gray-500 mt-1">{t('editor.timelineNote')}</p>
        </div>
      )}

      {showSpeedControl && (
        <div className="pt-4 border-t border-gray-700">
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-500">{t('editor.speed')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="20"
                max="500"
                step="10"
                key={`speed-${selectedVideoClip.speed ?? 1}`}
                defaultValue={Math.round((selectedVideoClip.speed ?? 1) * 100)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    const val = Math.max(20, Math.min(500, parseInt(e.currentTarget.value) || 100)) / 100
                    handleUpdateVideoClipDebounced({ speed: val })
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(20, Math.min(500, parseInt(e.target.value) || 100)) / 100
                  if (val !== (selectedVideoClip.speed ?? 1)) {
                    handleUpdateVideoClipDebounced({ speed: val })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">%</span>
            </div>
          </div>
          <input
            type="range"
            min="0.2"
            max="5"
            step="0.1"
            value={selectedVideoClip.speed ?? 1}
            onChange={(e) => handleUpdateVideoClipLocal({ speed: parseFloat(e.target.value) })}
            onMouseUp={(e) => handleUpdateVideoClipDebounced({ speed: parseFloat(e.currentTarget.value) })}
            onTouchEnd={(e) => handleUpdateVideoClipDebounced({ speed: parseFloat((e.target as HTMLInputElement).value) })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
      )}

      {isVideoAsset && (
        <div className="pt-4 border-t border-gray-700">
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-500">{t('editor.freezeFrame')}</label>
            <span className="text-xs text-gray-300">
              {((selectedVideoClip.freezeFrameMs ?? 0) / 1000).toFixed(1)}s
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="30"
            step="0.5"
            value={(selectedVideoClip.freezeFrameMs ?? 0) / 1000}
            onChange={(e) => {
              const ms = Math.round(parseFloat(e.target.value) * 1000)
              handleUpdateVideoClipLocal({ freeze_frame_ms: ms })
            }}
            onMouseUp={(e) => {
              const ms = Math.round(parseFloat(e.currentTarget.value) * 1000)
              handleUpdateVideoClip({ freeze_frame_ms: ms })
            }}
            onTouchEnd={(e) => {
              const ms = Math.round(parseFloat((e.target as HTMLInputElement).value) * 1000)
              handleUpdateVideoClip({ freeze_frame_ms: ms })
            }}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
      )}
    </>
  )
}
