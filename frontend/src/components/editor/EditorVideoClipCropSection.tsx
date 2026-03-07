import { useTranslation } from 'react-i18next'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'

interface EditorVideoClipCropSectionProps {
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  selectedVideoClip: SelectedVideoClipInfo
}

export default function EditorVideoClipCropSection({
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
  selectedVideoClip,
}: EditorVideoClipCropSectionProps) {
  const { t } = useTranslation('editor')
  const crop = selectedVideoClip.crop || { top: 0, right: 0, bottom: 0, left: 0 }

  return (
    <div className="pt-4 border-t border-gray-700">
      <label className="block text-xs text-gray-500 mb-3">{t('editor.crop')}</label>
      <div className="space-y-3">
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-600">{t('editor.cropTop')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="0"
                max="50"
                step="1"
                key={`crop-top-${crop.top}`}
                defaultValue={Math.round(crop.top * 100)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                  if (val !== crop.top) {
                    handleUpdateVideoClip({ crop: { ...crop, top: val } })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">%</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="0.5"
            step="0.01"
            value={crop.top}
            onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, top: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, top: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, top: parseFloat((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-600">{t('editor.cropBottom')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="0"
                max="50"
                step="1"
                key={`crop-bottom-${crop.bottom}`}
                defaultValue={Math.round(crop.bottom * 100)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                  if (val !== crop.bottom) {
                    handleUpdateVideoClip({ crop: { ...crop, bottom: val } })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">%</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="0.5"
            step="0.01"
            value={crop.bottom}
            onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, bottom: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, bottom: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, bottom: parseFloat((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-600">{t('editor.cropLeft')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="0"
                max="50"
                step="1"
                key={`crop-left-${crop.left}`}
                defaultValue={Math.round(crop.left * 100)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                  if (val !== crop.left) {
                    handleUpdateVideoClip({ crop: { ...crop, left: val } })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">%</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="0.5"
            step="0.01"
            value={crop.left}
            onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, left: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, left: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, left: parseFloat((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-gray-600">{t('editor.cropRight')}</label>
            <div className="flex items-center">
              <input
                type="number"
                min="0"
                max="50"
                step="1"
                key={`crop-right-${crop.right}`}
                defaultValue={Math.round(crop.right * 100)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.currentTarget.blur()
                  }
                }}
                onBlur={(e) => {
                  const val = Math.max(0, Math.min(50, parseInt(e.target.value) || 0)) / 100
                  if (val !== crop.right) {
                    handleUpdateVideoClip({ crop: { ...crop, right: val } })
                  }
                }}
                className="w-14 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded text-right"
              />
              <span className="text-xs text-gray-500 ml-1">%</span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="0.5"
            step="0.01"
            value={crop.right}
            onChange={(e) => handleUpdateVideoClipLocal({ crop: { ...crop, right: parseFloat(e.target.value) } })}
            onMouseUp={(e) => handleUpdateVideoClip({ crop: { ...crop, right: parseFloat(e.currentTarget.value) } })}
            onTouchEnd={(e) => handleUpdateVideoClip({ crop: { ...crop, right: parseFloat((e.target as HTMLInputElement).value) } })}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
        {(crop.top > 0 || crop.bottom > 0 || crop.left > 0 || crop.right > 0) && (
          <button
            onClick={() => handleUpdateVideoClip({ crop: { top: 0, right: 0, bottom: 0, left: 0 } })}
            className="w-full px-2 py-1 text-xs bg-gray-600 text-gray-300 rounded hover:bg-gray-500"
          >
            {t('editor.resetCrop')}
          </button>
        )}
      </div>
    </div>
  )
}
