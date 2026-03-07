import { useTranslation } from 'react-i18next'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaParameterControlsProps {
  chromaKey: ChromaKeyConfig
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
}

export default function EditorVideoClipChromaParameterControls({
  chromaKey,
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
}: EditorVideoClipChromaParameterControlsProps) {
  const { t } = useTranslation('editor')

  return (
    <>
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-600">{t('editor.similarity')}</label>
          <div className="flex items-center">
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              key={`sim-${chromaKey.similarity}`}
              defaultValue={Math.round(chromaKey.similarity * 100)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') {
                  const val = Math.max(0, Math.min(100, parseInt(e.currentTarget.value) || 0)) / 100
                  handleUpdateVideoClip({
                    effects: { chroma_key: { ...chromaKey, similarity: val } },
                  })
                  e.currentTarget.blur()
                }
              }}
              onBlur={(e) => {
                const val = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100
                if (val !== chromaKey.similarity) {
                  handleUpdateVideoClip({
                    effects: { chroma_key: { ...chromaKey, similarity: val } },
                  })
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
          max="1"
          step="0.01"
          value={chromaKey.similarity}
          onChange={(e) => handleUpdateVideoClipLocal({
            effects: { chroma_key: { ...chromaKey, similarity: parseFloat(e.target.value) } },
          })}
          onMouseUp={(e) => handleUpdateVideoClip({
            effects: { chroma_key: { ...chromaKey, similarity: parseFloat(e.currentTarget.value) } },
          })}
          onTouchEnd={(e) => handleUpdateVideoClip({
            effects: { chroma_key: { ...chromaKey, similarity: parseFloat((e.target as HTMLInputElement).value) } },
          })}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-600">{t('editor.blend')}</label>
          <div className="flex items-center">
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              key={`blend-${chromaKey.blend}`}
              defaultValue={Math.round(chromaKey.blend * 100)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') {
                  const val = Math.max(0, Math.min(100, parseInt(e.currentTarget.value) || 0)) / 100
                  handleUpdateVideoClip({
                    effects: { chroma_key: { ...chromaKey, blend: val } },
                  })
                  e.currentTarget.blur()
                }
              }}
              onBlur={(e) => {
                const val = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)) / 100
                if (val !== chromaKey.blend) {
                  handleUpdateVideoClip({
                    effects: { chroma_key: { ...chromaKey, blend: val } },
                  })
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
          max="1"
          step="0.01"
          value={chromaKey.blend}
          onChange={(e) => handleUpdateVideoClipLocal({
            effects: { chroma_key: { ...chromaKey, blend: parseFloat(e.target.value) } },
          })}
          onMouseUp={(e) => handleUpdateVideoClip({
            effects: { chroma_key: { ...chromaKey, blend: parseFloat(e.currentTarget.value) } },
          })}
          onTouchEnd={(e) => handleUpdateVideoClip({
            effects: { chroma_key: { ...chromaKey, blend: parseFloat((e.target as HTMLInputElement).value) } },
          })}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
        />
      </div>
    </>
  )
}
