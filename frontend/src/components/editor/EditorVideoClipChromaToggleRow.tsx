import { type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaToggleRowProps {
  chromaKey: ChromaKeyConfig
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  setChromaRenderOverlay: Dispatch<SetStateAction<string | null>>
  setChromaRenderOverlayDims: Dispatch<SetStateAction<{ width: number; height: number } | null>>
  setChromaRenderOverlayTimeMs: Dispatch<SetStateAction<number | null>>
}

export default function EditorVideoClipChromaToggleRow({
  chromaKey,
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
  setChromaRenderOverlay,
  setChromaRenderOverlayDims,
  setChromaRenderOverlayTimeMs,
}: EditorVideoClipChromaToggleRowProps) {
  const { t } = useTranslation('editor')

  return (
    <div className="flex items-center justify-between mb-2">
      <label className="text-xs text-gray-500">{t('editor.chromaKey')}</label>
      <button
        onClick={() => {
          const newChromaKey = {
            enabled: !chromaKey.enabled,
            color: chromaKey.color,
            similarity: chromaKey.similarity,
            blend: chromaKey.blend,
          }
          if (chromaKey.enabled) {
            setChromaRenderOverlay(null)
            setChromaRenderOverlayTimeMs(null)
            setChromaRenderOverlayDims(null)
          }
          handleUpdateVideoClipLocal({
            effects: { chroma_key: newChromaKey },
          })
          handleUpdateVideoClip({
            effects: { chroma_key: newChromaKey },
          })
        }}
        className={`px-2 py-0.5 text-xs rounded cursor-pointer transition-colors ${
          chromaKey.enabled
            ? 'bg-green-600 text-white hover:bg-green-700'
            : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
        }`}
      >
        {chromaKey.enabled ? 'ON' : 'OFF'}
      </button>
    </div>
  )
}
