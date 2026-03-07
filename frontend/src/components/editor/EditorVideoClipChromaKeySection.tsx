import { type Dispatch, type MouseEvent as ReactMouseEvent, type MutableRefObject, type SetStateAction } from 'react'
import { type ChromaKeyPreviewFrame } from '@/api/aiV1'
import EditorVideoClipChromaControls from '@/components/editor/EditorVideoClipChromaControls'
import EditorVideoClipChromaPreviewPanel from '@/components/editor/EditorVideoClipChromaPreviewPanel'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'

interface EditorVideoClipChromaKeySectionProps {
  chromaApplyLoading: boolean
  chromaColorBeforeEdit: string | null
  chromaPickerMode: boolean
  chromaPreviewError: string | null
  chromaPreviewFrames: ChromaKeyPreviewFrame[]
  chromaPreviewLoading: boolean
  chromaPreviewSelectedIndex: number | null
  chromaPreviewSize: number
  chromaRawFrameLoading: boolean
  compositeLightboxLoading: boolean
  handleChromaPreviewResizeStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  handleCompositePreview: () => void
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  projectId: string | null | undefined
  selectedVideoClip: SelectedVideoClipInfo
  setChromaColorBeforeEdit: Dispatch<SetStateAction<string | null>>
  setChromaPickerMode: Dispatch<SetStateAction<boolean>>
  setChromaPreviewError: Dispatch<SetStateAction<string | null>>
  setChromaPreviewSelectedIndex: Dispatch<SetStateAction<number | null>>
  setChromaRawFrame: Dispatch<SetStateAction<ChromaKeyPreviewFrame | null>>
  setChromaRawFrameLoading: Dispatch<SetStateAction<boolean>>
  setChromaRenderOverlay: Dispatch<SetStateAction<string | null>>
  setChromaRenderOverlayDims: Dispatch<SetStateAction<{ width: number; height: number } | null>>
  setChromaRenderOverlayTimeMs: Dispatch<SetStateAction<number | null>>
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorVideoClipChromaKeySection({
  chromaApplyLoading,
  chromaColorBeforeEdit,
  chromaPickerMode,
  chromaPreviewError,
  chromaPreviewFrames,
  chromaPreviewLoading,
  chromaPreviewSelectedIndex,
  chromaPreviewSize,
  chromaRawFrameLoading,
  compositeLightboxLoading,
  handleChromaPreviewResizeStart,
  handleCompositePreview,
  handleUpdateVideoClip,
  handleUpdateVideoClipLocal,
  projectId,
  selectedVideoClip,
  setChromaColorBeforeEdit,
  setChromaPickerMode,
  setChromaPreviewError,
  setChromaPreviewSelectedIndex,
  setChromaRawFrame,
  setChromaRawFrameLoading,
  setChromaRenderOverlay,
  setChromaRenderOverlayDims,
  setChromaRenderOverlayTimeMs,
  videoRefsMap,
}: EditorVideoClipChromaKeySectionProps) {
  const chromaKey = selectedVideoClip.effects.chroma_key || {
    enabled: false,
    color: '#00FF00',
    similarity: 0.05,
    blend: 0.0,
  }

  return (
    <div className="pt-4 border-t border-gray-700">
      <EditorVideoClipChromaControls
        chromaApplyLoading={chromaApplyLoading}
        chromaColorBeforeEdit={chromaColorBeforeEdit}
        chromaKey={chromaKey}
        chromaPickerMode={chromaPickerMode}
        chromaPreviewLoading={chromaPreviewLoading}
        chromaRawFrameLoading={chromaRawFrameLoading}
        handleUpdateVideoClip={handleUpdateVideoClip}
        handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
        projectId={projectId}
        selectedVideoClip={selectedVideoClip}
        setChromaColorBeforeEdit={setChromaColorBeforeEdit}
        setChromaPickerMode={setChromaPickerMode}
        setChromaPreviewError={setChromaPreviewError}
        setChromaRawFrame={setChromaRawFrame}
        setChromaRawFrameLoading={setChromaRawFrameLoading}
        setChromaRenderOverlay={setChromaRenderOverlay}
        setChromaRenderOverlayDims={setChromaRenderOverlayDims}
        setChromaRenderOverlayTimeMs={setChromaRenderOverlayTimeMs}
        videoRefsMap={videoRefsMap}
      />
      <EditorVideoClipChromaPreviewPanel
        chromaPreviewError={chromaPreviewError}
        chromaPreviewFrames={chromaPreviewFrames}
        chromaPreviewSelectedIndex={chromaPreviewSelectedIndex}
        chromaPreviewSize={chromaPreviewSize}
        compositeLightboxLoading={compositeLightboxLoading}
        handleChromaPreviewResizeStart={handleChromaPreviewResizeStart}
        handleCompositePreview={handleCompositePreview}
        setChromaPreviewSelectedIndex={setChromaPreviewSelectedIndex}
      />
    </div>
  )
}
