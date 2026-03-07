import { type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaManualColorInputProps {
  chromaColorBeforeEdit: string | null
  chromaKey: ChromaKeyConfig
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  setChromaColorBeforeEdit: Dispatch<SetStateAction<string | null>>
}

export default function EditorVideoClipChromaManualColorInput({
  chromaColorBeforeEdit,
  chromaKey,
  handleUpdateVideoClipLocal,
  setChromaColorBeforeEdit,
}: EditorVideoClipChromaManualColorInputProps) {
  const { t } = useTranslation('editor')

  return (
    <>
      <label className="text-xs text-gray-600 w-16">{t('editor.colorLabel')}</label>
      <input
        type="color"
        value={chromaKey.color}
        onFocus={() => {
          if (chromaColorBeforeEdit === null) {
            setChromaColorBeforeEdit(chromaKey.color)
          }
        }}
        onChange={(e) => {
          if (chromaColorBeforeEdit === null) {
            setChromaColorBeforeEdit(chromaKey.color)
          }
          handleUpdateVideoClipLocal({
            effects: { chroma_key: { ...chromaKey, color: e.target.value } },
          })
        }}
        className="w-8 h-8 rounded border border-gray-600 bg-transparent cursor-pointer"
      />
      <input
        type="text"
        value={chromaKey.color.toUpperCase()}
        onFocus={() => {
          if (chromaColorBeforeEdit === null) {
            setChromaColorBeforeEdit(chromaKey.color)
          }
        }}
        onChange={(e) => {
          let val = e.target.value.toUpperCase()
          if (!val.startsWith('#')) val = `#${val}`
          if (/^#[0-9A-F]{0,6}$/.test(val) || val === '#') {
            if (/^#[0-9A-F]{6}$/.test(val)) {
              if (chromaColorBeforeEdit === null) {
                setChromaColorBeforeEdit(chromaKey.color)
              }
              handleUpdateVideoClipLocal({
                effects: { chroma_key: { ...chromaKey, color: val } },
              })
            }
          }
        }}
        onKeyDown={(e) => {
          e.stopPropagation()
        }}
        onBlur={() => {}}
        className="w-20 px-1 py-0.5 text-xs text-white bg-gray-700 border border-gray-600 rounded font-mono"
        placeholder="#00FF00"
      />
    </>
  )
}
