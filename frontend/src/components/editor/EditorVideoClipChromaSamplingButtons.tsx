import { type Dispatch, type MutableRefObject, type SetStateAction, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { aiV1Api, type ChromaKeyPreviewFrame } from '@/api/aiV1'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaSamplingButtonsProps {
  chromaApplyLoading: boolean
  chromaColorBeforeEdit: string | null
  chromaKey: ChromaKeyConfig
  chromaPickerMode: boolean
  chromaPreviewLoading: boolean
  chromaRawFrameLoading: boolean
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

export default function EditorVideoClipChromaSamplingButtons({
  chromaApplyLoading,
  chromaColorBeforeEdit,
  chromaKey,
  chromaPickerMode,
  chromaPreviewLoading,
  chromaRawFrameLoading,
  handleUpdateVideoClipLocal,
  projectId,
  selectedVideoClip,
  setChromaColorBeforeEdit,
  setChromaPickerMode,
  setChromaPreviewError,
  setChromaRawFrame,
  setChromaRawFrameLoading,
  videoRefsMap,
}: EditorVideoClipChromaSamplingButtonsProps) {
  const { t } = useTranslation('editor')
  const [autoDetectLoading, setAutoDetectLoading] = useState(false)

  return (
    <>
      <button
        onClick={async () => {
          if (!projectId || autoDetectLoading) return

          setAutoDetectLoading(true)
          setChromaPreviewError(null)

          try {
            // Use backend sampler (4-corner analysis with quantization)
            // instead of local 10x10 pixel average which is inaccurate.
            // Pass current preview time so backend samples the frame the user is viewing.
            const video = videoRefsMap.current.get(selectedVideoClip.clipId)
            const videoTimeMs = video ? Math.round(video.currentTime * 1000) : 0
            const timeMs = selectedVideoClip.startMs + videoTimeMs

            const result = await aiV1Api.chromaKeyPreview(projectId, selectedVideoClip.clipId, {
              key_color: 'auto',
              similarity: chromaKey.similarity,
              blend: chromaKey.blend,
              resolution: '640x360',
              time_ms: timeMs,
            })

            if (result.resolved_key_color) {
              if (chromaColorBeforeEdit === null) {
                setChromaColorBeforeEdit(chromaKey.color)
              }
              handleUpdateVideoClipLocal({
                effects: { chroma_key: { ...chromaKey, color: result.resolved_key_color } },
              })
            }
          } catch (err) {
            const message =
              (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data
                ?.error?.message
              || (err as Error).message
              || 'Auto-detect failed'
            setChromaPreviewError(message)
          } finally {
            setAutoDetectLoading(false)
          }
        }}
        disabled={autoDetectLoading || chromaPreviewLoading || chromaApplyLoading}
        className={`px-2 py-1 text-xs rounded ${
          autoDetectLoading
            ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
            : 'bg-gray-600 text-gray-300 hover:bg-gray-500'
        }`}
        title={t('editor.auto')}
      >
        {autoDetectLoading ? t('transcription.processing') : t('editor.auto')}
      </button>
      <button
        onClick={async () => {
          if (!projectId) return

          setChromaRawFrameLoading(true)
          setChromaPreviewError(null)
          setChromaRawFrame(null)

          try {
            const result = await aiV1Api.chromaKeyPreview(projectId, selectedVideoClip.clipId, {
              key_color: chromaKey.color,
              similarity: chromaKey.similarity,
              blend: chromaKey.blend,
              resolution: '640x360',
              skip_chroma_key: true,
            })

            if (result.frames.length > 0) {
              setChromaRawFrame(result.frames[0])
              if (chromaColorBeforeEdit === null) {
                setChromaColorBeforeEdit(chromaKey.color)
              }
              setChromaPickerMode(true)
            }
          } catch (err) {
            const message =
              (err as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error?.message
              || (err as Error).message
              || t('editor.compositePreview')
            setChromaPreviewError(message)
          } finally {
            setChromaRawFrameLoading(false)
          }
        }}
        disabled={chromaPreviewLoading || chromaApplyLoading || chromaRawFrameLoading}
        className={`px-2 py-1 text-xs rounded ${
          chromaPickerMode
            ? 'bg-yellow-600 text-white'
            : chromaPreviewLoading || chromaApplyLoading || chromaRawFrameLoading
              ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
              : 'bg-purple-600 text-white hover:bg-purple-700'
        }`}
        title={t('editor.dropper')}
      >
        {chromaRawFrameLoading ? t('transcription.processing') : t('editor.dropper')}
      </button>
    </>
  )
}
