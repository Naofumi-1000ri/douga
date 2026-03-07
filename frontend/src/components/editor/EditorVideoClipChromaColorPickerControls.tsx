import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { type ChromaKeyPreviewFrame } from '@/api/aiV1'
import EditorVideoClipChromaManualColorInput from '@/components/editor/EditorVideoClipChromaManualColorInput'
import EditorVideoClipChromaSamplingButtons from '@/components/editor/EditorVideoClipChromaSamplingButtons'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaColorPickerControlsProps {
  chromaApplyLoading: boolean
  chromaColorBeforeEdit: string | null
  chromaKey: ChromaKeyConfig
  chromaPickerMode: boolean
  chromaPreviewLoading: boolean
  chromaRawFrameLoading: boolean
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  projectId: string | null | undefined
  selectedVideoClip: SelectedVideoClipInfo
  setChromaColorBeforeEdit: Dispatch<SetStateAction<string | null>>
  setChromaPickerMode: Dispatch<SetStateAction<boolean>>
  setChromaPreviewError: Dispatch<SetStateAction<string | null>>
  setChromaRawFrame: Dispatch<SetStateAction<ChromaKeyPreviewFrame | null>>
  setChromaRawFrameLoading: Dispatch<SetStateAction<boolean>>
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorVideoClipChromaColorPickerControls({
  chromaApplyLoading,
  chromaColorBeforeEdit,
  chromaKey,
  chromaPickerMode,
  chromaPreviewLoading,
  chromaRawFrameLoading,
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
  projectId,
  selectedVideoClip,
  setChromaColorBeforeEdit,
  setChromaPickerMode,
  setChromaPreviewError,
  setChromaRawFrame,
  setChromaRawFrameLoading,
  videoRefsMap,
}: EditorVideoClipChromaColorPickerControlsProps) {
  const { t } = useTranslation('editor')

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <EditorVideoClipChromaManualColorInput
          chromaColorBeforeEdit={chromaColorBeforeEdit}
          chromaKey={chromaKey}
          handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
          setChromaColorBeforeEdit={setChromaColorBeforeEdit}
        />
        <EditorVideoClipChromaSamplingButtons
          chromaApplyLoading={chromaApplyLoading}
          chromaColorBeforeEdit={chromaColorBeforeEdit}
          chromaKey={chromaKey}
          chromaPickerMode={chromaPickerMode}
          chromaPreviewLoading={chromaPreviewLoading}
          chromaRawFrameLoading={chromaRawFrameLoading}
          handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
          projectId={projectId}
          selectedVideoClip={selectedVideoClip}
          setChromaColorBeforeEdit={setChromaColorBeforeEdit}
          setChromaPickerMode={setChromaPickerMode}
          setChromaPreviewError={setChromaPreviewError}
          setChromaRawFrame={setChromaRawFrame}
          setChromaRawFrameLoading={setChromaRawFrameLoading}
          videoRefsMap={videoRefsMap}
        />
      </div>
      <div className="flex items-center gap-2 ml-16">
        <button
          onClick={() => {
            if (chromaColorBeforeEdit !== null) {
              handleUpdateVideoClipLocal({
                effects: { chroma_key: { ...chromaKey, color: chromaColorBeforeEdit } },
              })
              setChromaColorBeforeEdit(null)
            }
          }}
          disabled={chromaColorBeforeEdit === null}
          className={`px-2 py-1 text-xs rounded ${
            chromaColorBeforeEdit === null
              ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-gray-600 text-gray-300 hover:bg-gray-500 cursor-pointer'
          }`}
          title={t('editor.cancel')}
        >
          Cancel
        </button>
        <button
          onClick={() => {
            handleUpdateVideoClip({
              effects: { chroma_key: { ...chromaKey, color: chromaKey.color } },
            })
            setChromaColorBeforeEdit(null)
          }}
          disabled={chromaColorBeforeEdit === null}
          className={`px-2 py-1 text-xs rounded ${
            chromaColorBeforeEdit === null
              ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-green-600 text-white hover:bg-green-700 cursor-pointer'
          }`}
          title={t('editor.confirm')}
        >
          Apply
        </button>
      </div>
    </div>
  )
}
