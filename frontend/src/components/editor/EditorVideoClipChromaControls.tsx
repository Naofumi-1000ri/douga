import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { type ChromaKeyPreviewFrame } from '@/api/aiV1'
import EditorVideoClipChromaColorControls from '@/components/editor/EditorVideoClipChromaColorControls'
import EditorVideoClipChromaParameterControls from '@/components/editor/EditorVideoClipChromaParameterControls'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import type { ChromaKeyConfig } from '@/components/editor/editorVideoClipChromaShared'

interface EditorVideoClipChromaControlsProps {
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
  setChromaRenderOverlay: Dispatch<SetStateAction<string | null>>
  setChromaRenderOverlayDims: Dispatch<SetStateAction<{ width: number; height: number } | null>>
  setChromaRenderOverlayTimeMs: Dispatch<SetStateAction<number | null>>
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorVideoClipChromaControls({
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
  setChromaRenderOverlay,
  setChromaRenderOverlayDims,
  setChromaRenderOverlayTimeMs,
  videoRefsMap,
}: EditorVideoClipChromaControlsProps) {
  return (
    <div className="space-y-3">
      <EditorVideoClipChromaColorControls
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
      <EditorVideoClipChromaParameterControls
        chromaKey={chromaKey}
        handleUpdateVideoClip={handleUpdateVideoClip}
        handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
      />
    </div>
  )
}
