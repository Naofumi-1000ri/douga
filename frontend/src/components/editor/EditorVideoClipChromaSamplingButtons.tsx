import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
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

  return (
    <>
      <button
        onClick={() => {
          const video = videoRefsMap.current.get(selectedVideoClip.clipId)
          if (!video) return
          const canvas = document.createElement('canvas')
          canvas.width = video.videoWidth
          canvas.height = video.videoHeight
          const ctx = canvas.getContext('2d')
          if (!ctx) return
          try {
            ctx.drawImage(video, 0, 0)
            const imageData = ctx.getImageData(0, 0, 10, 10)
            let r = 0
            let g = 0
            let b = 0
            for (let i = 0; i < imageData.data.length; i += 4) {
              r += imageData.data[i]
              g += imageData.data[i + 1]
              b += imageData.data[i + 2]
            }
            const count = imageData.data.length / 4
            r = Math.round(r / count)
            g = Math.round(g / count)
            b = Math.round(b / count)
            const hex = `#${[r, g, b].map((value) => value.toString(16).padStart(2, '0')).join('').toUpperCase()}`
            if (chromaColorBeforeEdit === null) {
              setChromaColorBeforeEdit(chromaKey.color)
            }
            handleUpdateVideoClipLocal({
              effects: { chroma_key: { ...chromaKey, color: hex } },
            })
          } catch (err) {
            console.error('Failed to sample color:', err)
          }
        }}
        className="px-2 py-1 text-xs bg-gray-600 text-gray-300 rounded hover:bg-gray-500"
        title={t('editor.auto')}
      >
        {t('editor.auto')}
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
