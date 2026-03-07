import { type Dispatch, type MouseEvent as ReactMouseEvent, type MutableRefObject, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { type ChromaKeyPreviewFrame } from '@/api/aiV1'
import type { Asset } from '@/api/assets'
import EditorVideoClipChromaKeySection from '@/components/editor/EditorVideoClipChromaKeySection'
import EditorVideoClipCropSection from '@/components/editor/EditorVideoClipCropSection'
import EditorVideoClipDetailsSection from '@/components/editor/EditorVideoClipDetailsSection'
import EditorVideoClipShapeSection from '@/components/editor/EditorVideoClipShapeSection'
import EditorVideoClipTransformSection from '@/components/editor/EditorVideoClipTransformSection'
import EditorTextClipInspector from '@/components/editor/EditorTextClipInspector'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'

type UpdateHandler = (updates: Record<string, unknown>) => void

interface ChromaState {
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
  projectId: string | null | undefined
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

interface KeyframeControls {
  currentKeyframeExists: () => boolean
  getCurrentInterpolatedValues: () => { x: number; y: number; scale: number; rotation: number } | null
  handleAddKeyframe: () => void
  handleRemoveKeyframe: () => void
  selectedKeyframeIndex: number | null
  setSelectedKeyframeIndex: Dispatch<SetStateAction<number | null>>
}

interface ShapeControls {
  handleUpdateShape: UpdateHandler
  handleUpdateShapeDebounced: UpdateHandler
  handleUpdateShapeFade: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeFadeLocal: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeLocal: UpdateHandler
}

interface TextControls {
  isComposing: boolean
  localTextContent: string
  setIsComposing: Dispatch<SetStateAction<boolean>>
  setLocalTextContent: Dispatch<SetStateAction<string>>
  textDebounceRef: MutableRefObject<ReturnType<typeof setTimeout> | null>
}

interface VideoControls {
  handleCompositePreview: () => void
  handleDeleteVideoClip: () => void
  handleFitFillStretch: (mode: 'fit' | 'fill' | 'stretch') => void
  handleUpdateVideoClip: UpdateHandler
  handleUpdateVideoClipDebounced: UpdateHandler
  handleUpdateVideoClipLocal: UpdateHandler
  handleUpdateVideoClipTiming: (updates: { startMs?: number; durationMs?: number }) => void
}

interface EditorVideoClipInspectorProps {
  assets: Asset[]
  chromaState: ChromaState
  keyframeControls: KeyframeControls
  selectedVideoClip: SelectedVideoClipInfo
  shapeControls: ShapeControls
  textControls: TextControls
  videoControls: VideoControls
}

export default function EditorVideoClipInspector({
  assets,
  chromaState,
  keyframeControls,
  selectedVideoClip,
  shapeControls,
  textControls,
  videoControls,
}: EditorVideoClipInspectorProps) {
  const { t } = useTranslation('editor')
  const {
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
    projectId,
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
  } = chromaState
  const {
    currentKeyframeExists,
    getCurrentInterpolatedValues,
    handleAddKeyframe,
    handleRemoveKeyframe,
    selectedKeyframeIndex,
    setSelectedKeyframeIndex,
  } = keyframeControls
  const {
    handleUpdateShape,
    handleUpdateShapeDebounced,
    handleUpdateShapeFade,
    handleUpdateShapeFadeLocal,
    handleUpdateShapeLocal,
  } = shapeControls
  const {
    isComposing,
    localTextContent,
    setIsComposing,
    setLocalTextContent,
    textDebounceRef,
  } = textControls
  const {
    handleCompositePreview,
    handleDeleteVideoClip,
    handleFitFillStretch,
    handleUpdateVideoClip,
    handleUpdateVideoClipDebounced,
    handleUpdateVideoClipLocal,
    handleUpdateVideoClipTiming,
  } = videoControls

  const clipAsset = assets.find((asset) => asset.id === selectedVideoClip.assetId)
  const isVideoAsset = clipAsset?.type === 'video'
  const supportsCrop = clipAsset?.type === 'video' || clipAsset?.type === 'image'

  return (
    <div className="space-y-4">
      <EditorVideoClipDetailsSection
        clipAsset={clipAsset}
        handleUpdateVideoClip={handleUpdateVideoClip}
        handleUpdateVideoClipDebounced={handleUpdateVideoClipDebounced}
        handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
        handleUpdateVideoClipTiming={handleUpdateVideoClipTiming}
        selectedVideoClip={selectedVideoClip}
      />

      <EditorVideoClipTransformSection
        currentKeyframeExists={currentKeyframeExists}
        getCurrentInterpolatedValues={getCurrentInterpolatedValues}
        handleAddKeyframe={handleAddKeyframe}
        handleFitFillStretch={handleFitFillStretch}
        handleRemoveKeyframe={handleRemoveKeyframe}
        handleUpdateVideoClip={handleUpdateVideoClip}
        handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
        selectedKeyframeIndex={selectedKeyframeIndex}
        selectedVideoClip={selectedVideoClip}
        setSelectedKeyframeIndex={setSelectedKeyframeIndex}
      />

      {isVideoAsset && (
        <EditorVideoClipChromaKeySection
          chromaApplyLoading={chromaApplyLoading}
          chromaColorBeforeEdit={chromaColorBeforeEdit}
          chromaPickerMode={chromaPickerMode}
          chromaPreviewError={chromaPreviewError}
          chromaPreviewFrames={chromaPreviewFrames}
          chromaPreviewLoading={chromaPreviewLoading}
          chromaPreviewSelectedIndex={chromaPreviewSelectedIndex}
          chromaPreviewSize={chromaPreviewSize}
          chromaRawFrameLoading={chromaRawFrameLoading}
          compositeLightboxLoading={compositeLightboxLoading}
          handleChromaPreviewResizeStart={handleChromaPreviewResizeStart}
          handleCompositePreview={handleCompositePreview}
          handleUpdateVideoClip={handleUpdateVideoClip}
          handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
          projectId={projectId}
          selectedVideoClip={selectedVideoClip}
          setChromaColorBeforeEdit={setChromaColorBeforeEdit}
          setChromaPickerMode={setChromaPickerMode}
          setChromaPreviewError={setChromaPreviewError}
          setChromaPreviewSelectedIndex={setChromaPreviewSelectedIndex}
          setChromaRawFrame={setChromaRawFrame}
          setChromaRawFrameLoading={setChromaRawFrameLoading}
          setChromaRenderOverlay={setChromaRenderOverlay}
          setChromaRenderOverlayDims={setChromaRenderOverlayDims}
          setChromaRenderOverlayTimeMs={setChromaRenderOverlayTimeMs}
          videoRefsMap={videoRefsMap}
        />
      )}

      {supportsCrop && (
        <EditorVideoClipCropSection
          handleUpdateVideoClip={handleUpdateVideoClip}
          handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
          selectedVideoClip={selectedVideoClip}
        />
      )}

      {selectedVideoClip.shape && (
        <EditorVideoClipShapeSection
          handleUpdateShape={handleUpdateShape}
          handleUpdateShapeDebounced={handleUpdateShapeDebounced}
          handleUpdateShapeFade={handleUpdateShapeFade}
          handleUpdateShapeFadeLocal={handleUpdateShapeFadeLocal}
          handleUpdateShapeLocal={handleUpdateShapeLocal}
          selectedVideoClip={selectedVideoClip}
        />
      )}

      {selectedVideoClip.textContent !== undefined && (
        <EditorTextClipInspector
          handleUpdateVideoClip={handleUpdateVideoClip}
          handleUpdateVideoClipDebounced={handleUpdateVideoClipDebounced}
          handleUpdateVideoClipLocal={handleUpdateVideoClipLocal}
          isComposing={isComposing}
          localTextContent={localTextContent}
          selectedVideoClip={selectedVideoClip}
          setIsComposing={setIsComposing}
          setLocalTextContent={setLocalTextContent}
          textDebounceRef={textDebounceRef}
        />
      )}

      {selectedVideoClip.assetId && (
        <div className="pt-4 border-t border-gray-700">
          <label className="block text-xs text-gray-500 mb-1">{t('editor.assetId')}</label>
          <p className="text-gray-400 text-xs font-mono break-all">{selectedVideoClip.assetId}</p>
        </div>
      )}

      <div className="pt-4 border-t border-gray-700">
        <button
          onClick={handleDeleteVideoClip}
          className="w-full px-3 py-2 text-sm text-red-400 hover:text-white hover:bg-red-600 border border-red-600 rounded transition-colors"
        >
          {t('editor.deleteClip')}
        </button>
      </div>
    </div>
  )
}
