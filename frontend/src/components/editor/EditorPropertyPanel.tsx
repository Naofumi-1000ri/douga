import { type Dispatch, type MouseEvent as ReactMouseEvent, type MutableRefObject, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChromaKeyPreviewFrame } from '@/api/aiV1'
import type { Asset } from '@/api/assets'
import EditorAudioClipInspector from '@/components/editor/EditorAudioClipInspector'
import EditorVideoClipInspector from '@/components/editor/EditorVideoClipInspector'
import type { SelectedClipInfo, SelectedVideoClipInfo } from '@/components/editor/Timeline'
import type { TimelineData } from '@/store/projectStore'

interface NewVolumeKeyframeInput {
  timeMs: string
  volume: string
}

interface EditorPropertyPanelProps {
  assets: Asset[]
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
  currentKeyframeExists: () => boolean
  currentTime: number
  getCurrentInterpolatedValues: () => { x: number; y: number; scale: number; rotation: number } | null
  handleAddKeyframe: (...args: unknown[]) => void
  handleAddVolumeKeyframeAtCurrent: (volume: number) => void
  handleAddVolumeKeyframeManual: (timeMs: number, volume: number) => void
  handleChromaPreviewResizeStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  handleClearVolumeKeyframes: () => void
  handleCompositePreview: () => void
  handleDeleteVideoClip: () => void
  handleFitFillStretch: (mode: 'fit' | 'fill' | 'stretch') => void
  handleRemoveKeyframe: (...args: unknown[]) => void
  handleRemoveVolumeKeyframe: (index: number) => void
  handleRightPanelResizeStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  handleUpdateAudioClip: (updates: Record<string, unknown>) => void
  handleUpdateShape: (updates: Record<string, unknown>) => void
  handleUpdateShapeDebounced: (updates: Record<string, unknown>) => void
  handleUpdateShapeFade: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeFadeLocal: (updates: { fadeInMs?: number; fadeOutMs?: number }) => void
  handleUpdateShapeLocal: (updates: Record<string, unknown>) => void
  handleUpdateVideoClip: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipDebounced: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipLocal: (updates: Record<string, unknown>) => void
  handleUpdateVideoClipTiming: (updates: { startMs?: number; durationMs?: number }) => void
  handleUpdateVolumeKeyframe: (index: number, timeMs: number, value: number) => void
  isPropertyPanelOpen: boolean
  isComposing: boolean
  localTextContent: string
  newKeyframeInput: NewVolumeKeyframeInput
  projectId: string | null | undefined
  rightPanelWidth: number
  selectedClip: SelectedClipInfo | null
  selectedKeyframeIndex: number | null
  selectedVideoClip: SelectedVideoClipInfo | null
  setChromaColorBeforeEdit: Dispatch<SetStateAction<string | null>>
  setChromaPickerMode: Dispatch<SetStateAction<boolean>>
  setChromaPreviewError: Dispatch<SetStateAction<string | null>>
  setChromaPreviewSelectedIndex: Dispatch<SetStateAction<number | null>>
  setChromaRawFrame: Dispatch<SetStateAction<ChromaKeyPreviewFrame | null>>
  setChromaRawFrameLoading: Dispatch<SetStateAction<boolean>>
  setChromaRenderOverlay: Dispatch<SetStateAction<string | null>>
  setChromaRenderOverlayDims: Dispatch<SetStateAction<{ width: number; height: number } | null>>
  setChromaRenderOverlayTimeMs: Dispatch<SetStateAction<number | null>>
  setIsComposing: Dispatch<SetStateAction<boolean>>
  setIsPropertyPanelOpen: Dispatch<SetStateAction<boolean>>
  setLocalTextContent: Dispatch<SetStateAction<string>>
  setNewKeyframeInput: Dispatch<SetStateAction<NewVolumeKeyframeInput>>
  setSelectedKeyframeIndex: Dispatch<SetStateAction<number | null>>
  textDebounceRef: MutableRefObject<ReturnType<typeof setTimeout> | null>
  timelineData?: TimelineData
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorPropertyPanel({
  assets,
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
  currentKeyframeExists,
  currentTime,
  getCurrentInterpolatedValues,
  handleAddKeyframe,
  handleAddVolumeKeyframeAtCurrent,
  handleAddVolumeKeyframeManual,
  handleChromaPreviewResizeStart,
  handleClearVolumeKeyframes,
  handleCompositePreview,
  handleDeleteVideoClip,
  handleFitFillStretch,
  handleRemoveKeyframe,
  handleRemoveVolumeKeyframe,
  handleRightPanelResizeStart,
  handleUpdateAudioClip,
  handleUpdateShape,
  handleUpdateShapeDebounced,
  handleUpdateShapeFade,
  handleUpdateShapeFadeLocal,
  handleUpdateShapeLocal,
  handleUpdateVideoClip,
  handleUpdateVideoClipDebounced,
  handleUpdateVideoClipLocal,
  handleUpdateVideoClipTiming,
  handleUpdateVolumeKeyframe,
  isPropertyPanelOpen,
  isComposing,
  localTextContent,
  newKeyframeInput,
  projectId,
  rightPanelWidth,
  selectedClip,
  selectedKeyframeIndex,
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
  setIsComposing,
  setIsPropertyPanelOpen,
  setLocalTextContent,
  setNewKeyframeInput,
  setSelectedKeyframeIndex,
  textDebounceRef,
  timelineData,
  videoRefsMap,
}: EditorPropertyPanelProps) {
  const { t } = useTranslation('editor')

  return (
    <>
      {isPropertyPanelOpen ? (
        <div
          data-testid="right-panel"
          className="bg-gray-800 border-l border-gray-700 flex flex-col relative"
          style={{ width: rightPanelWidth }}
        >
          <div
            className="absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-blue-500/50 active:bg-blue-500 transition-colors z-10"
            onMouseDown={handleRightPanelResizeStart}
          />
          <div
            onClick={() => setIsPropertyPanelOpen(false)}
            className="flex items-center justify-between px-3 py-2 border-b border-gray-700 cursor-pointer hover:bg-gray-700 transition-colors flex-shrink-0"
          >
            <h2 className="text-white font-medium text-sm">{t('editor.properties')}</h2>
            <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </div>
          <div className="flex-1 overflow-y-auto p-4" style={{ scrollbarGutter: 'stable' }}>
            {selectedVideoClip ? (
              <EditorVideoClipInspector
                assets={assets}
                chromaState={{
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
                }}
                keyframeControls={{
                  currentKeyframeExists,
                  getCurrentInterpolatedValues,
                  handleAddKeyframe,
                  handleRemoveKeyframe,
                  selectedKeyframeIndex,
                  setSelectedKeyframeIndex,
                }}
                selectedVideoClip={selectedVideoClip}
                shapeControls={{
                  handleUpdateShape,
                  handleUpdateShapeDebounced,
                  handleUpdateShapeFade,
                  handleUpdateShapeFadeLocal,
                  handleUpdateShapeLocal,
                }}
                textControls={{
                  isComposing,
                  localTextContent,
                  setIsComposing,
                  setLocalTextContent,
                  textDebounceRef,
                }}
                videoControls={{
                  handleCompositePreview,
                  handleDeleteVideoClip,
                  handleFitFillStretch,
                  handleUpdateVideoClip,
                  handleUpdateVideoClipDebounced,
                  handleUpdateVideoClipLocal,
                  handleUpdateVideoClipTiming,
                }}
              />
            ) : selectedClip ? (
              <EditorAudioClipInspector
                currentTime={currentTime}
                handleAddVolumeKeyframeAtCurrent={handleAddVolumeKeyframeAtCurrent}
                handleAddVolumeKeyframeManual={handleAddVolumeKeyframeManual}
                handleClearVolumeKeyframes={handleClearVolumeKeyframes}
                handleRemoveVolumeKeyframe={handleRemoveVolumeKeyframe}
                handleUpdateAudioClip={handleUpdateAudioClip}
                handleUpdateVolumeKeyframe={handleUpdateVolumeKeyframe}
                newKeyframeInput={newKeyframeInput}
                selectedClip={selectedClip}
                setNewKeyframeInput={setNewKeyframeInput}
                timelineData={timelineData}
              />
            ) : (
              <p className="text-gray-400 text-sm">{t('editor.selectElement')}</p>
            )}
          </div>
        </div>
      ) : (
        <div
          onClick={() => setIsPropertyPanelOpen(true)}
          className="bg-gray-800 border-l border-gray-700 w-11 flex flex-col items-center py-3 cursor-pointer group transition-colors hover:bg-gray-700/50"
        >
          <svg className="w-5 h-5 text-gray-500 group-hover:text-gray-300 transition-colors mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors" style={{ writingMode: 'vertical-rl' }}>
            {t('editor.propertyPanel')}
          </span>
        </div>
      )}
    </>
  )
}
