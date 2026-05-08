import { type MouseEvent as ReactMouseEvent, type MutableRefObject } from 'react'
import type { Asset } from '@/api/assets'
import EditorPreviewMediaClip from '@/components/editor/EditorPreviewMediaClip'
import EditorPreviewShapeClip from '@/components/editor/EditorPreviewShapeClip'
import EditorPreviewTextClip from '@/components/editor/EditorPreviewTextClip'
import { buildActivePreviewClips } from '@/components/editor/editorPreviewStageShared'
import type { SelectedVideoClipInfo } from '@/components/editor/Timeline'
import type { PreviewState } from '@/hooks/useAssetPreviewWorkflow'
import type { PreviewDragHandle, PreviewDragState, PreviewDragTransform, PreviewSnapGuide } from '@/hooks/usePreviewDragWorkflow'
import type { Clip, ProjectDetail, TimelineData } from '@/store/projectStore'

interface EditorPreviewStageProps {
  assetUrlCache: Map<string, string>
  assets: Asset[]
  chromaRenderOverlay: string | null
  chromaRenderOverlayDims: { width: number; height: number } | null
  currentProject: Pick<ProjectDetail, 'width' | 'height'>
  currentTime: number
  dragCrop: Clip['crop'] | null
  dragTransform: PreviewDragTransform | null
  effectivePreviewHeight: number
  effectivePreviewWidth: number
  handlePreviewDragStart: (
    event: ReactMouseEvent,
    type: PreviewDragHandle,
    layerId: string,
    clipId: string,
  ) => void
  invalidateAssetUrl: (assetId: string) => void
  isPlaying: boolean
  onDeselect: () => void
  preview: PreviewState
  previewBorderColor: string
  previewBorderWidth: number
  previewDrag: PreviewDragState | null
  previewZoom: number
  selectedVideoClip: SelectedVideoClipInfo | null
  snapGuides: PreviewSnapGuide[]
  syncVideoToTimelinePosition: (
    video: HTMLVideoElement,
    clip: Pick<Clip, 'start_ms' | 'duration_ms' | 'in_point_ms' | 'speed' | 'freeze_frame_ms'>,
  ) => void
  timelineData?: TimelineData
  videoRefsMap: MutableRefObject<Map<string, HTMLVideoElement>>
}

export default function EditorPreviewStage({
  assetUrlCache,
  assets,
  chromaRenderOverlay,
  chromaRenderOverlayDims,
  currentProject,
  currentTime,
  dragCrop,
  dragTransform,
  effectivePreviewHeight,
  effectivePreviewWidth,
  handlePreviewDragStart,
  invalidateAssetUrl,
  isPlaying,
  onDeselect,
  preview,
  previewBorderColor,
  previewBorderWidth,
  previewDrag,
  previewZoom,
  selectedVideoClip,
  snapGuides,
  syncVideoToTimelinePosition,
  timelineData,
  videoRefsMap,
}: EditorPreviewStageProps) {
  const aspectRatio = currentProject.width / currentProject.height
  const baseHeight = Math.min(effectivePreviewHeight, effectivePreviewWidth / aspectRatio)
  const containerHeight = baseHeight * previewZoom
  const containerWidth = containerHeight * aspectRatio
  const previewScale = Math.min(containerWidth / currentProject.width, containerHeight / currentProject.height)

  const activeClips = buildActivePreviewClips({
    assets,
    currentTime,
    dragTransform,
    previewDrag,
    timelineData,
    selectedClipId: selectedVideoClip?.clipId ?? null,
  })
  const activeClipEntries = new Map(activeClips.map((activeClip, index) => [activeClip.clip.id, { activeClip, index }]))

  return (
    <div
      className="absolute inset-0 origin-top-left"
      style={{
        width: currentProject.width,
        height: currentProject.height,
        transform: `scale(${previewScale})`,
      }}
    >
      {previewBorderWidth > 0 && (
        <div
          className="absolute pointer-events-none"
          style={{
            inset: -previewBorderWidth,
            border: `${previewBorderWidth}px solid ${previewBorderColor}`,
            zIndex: 9999,
          }}
        />
      )}

      {activeClips.length > 0 && (
        <div className="absolute inset-0 bg-black" style={{ zIndex: 1 }} onClick={onDeselect} />
      )}

      {(timelineData?.layers ?? []).slice().reverse().flatMap((layer) => {
        if (layer.visible === false) return []

        return layer.clips.map((clip) => {
          const activeEntry = activeClipEntries.get(clip.id)

          if (activeEntry?.activeClip.shape) {
            const { activeClip, index } = activeEntry
            const isSelected = selectedVideoClip?.clipId === activeClip.clip.id
            const isDragging = previewDrag?.clipId === activeClip.clip.id
            const zIndex = index + 10

            return (
              <EditorPreviewShapeClip
                key={`persistent-${clip.id}`}
                activeClip={activeClip}
                handlePreviewDragStart={handlePreviewDragStart}
                isDragging={isDragging}
                isSelected={isSelected}
                zIndex={zIndex}
              />
            )
          }

          if (activeEntry?.activeClip.clip.text_content !== undefined) {
            const { activeClip, index } = activeEntry
            const isSelected = selectedVideoClip?.clipId === activeClip.clip.id
            const isDragging = previewDrag?.clipId === activeClip.clip.id
            const zIndex = index + 10

            return (
              <EditorPreviewTextClip
                key={`persistent-${clip.id}`}
                activeClip={activeClip}
                handlePreviewDragStart={handlePreviewDragStart}
                isDragging={isDragging}
                isSelected={isSelected}
                zIndex={zIndex}
              />
            )
          }

          if (!clip.asset_id) return null
          const asset = assets.find((candidate) => candidate.id === clip.asset_id)
          if (!asset) return null
          const url = assetUrlCache.get(clip.asset_id)
          if (!url) return null
          if (asset.type !== 'image' && asset.type !== 'video') return null

          const activeClip = activeEntry?.activeClip ?? null
          const isSelected = activeClip ? selectedVideoClip?.clipId === activeClip.clip.id : false
          const isDragging = activeClip ? previewDrag?.clipId === activeClip.clip.id : false
          const zIndex = activeEntry ? activeEntry.index + 10 : -1

          return (
            <EditorPreviewMediaClip
              key={`persistent-${clip.id}`}
              activeClip={activeClip}
              asset={asset}
              clip={clip}
              chromaRenderOverlay={chromaRenderOverlay}
              chromaRenderOverlayDims={chromaRenderOverlayDims}
              dragCrop={dragCrop}
              handlePreviewDragStart={handlePreviewDragStart}
              invalidateAssetUrl={invalidateAssetUrl}
              isDragging={isDragging}
              isPlaying={isPlaying}
              isSelected={isSelected}
              previewDrag={previewDrag}
              syncVideoToTimelinePosition={syncVideoToTimelinePosition}
              url={url}
              videoRefsMap={videoRefsMap}
              zIndex={zIndex}
            />
          )
        })
      })}

      {preview.url && preview.asset?.type === 'audio' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-400 bg-black">
          <svg className="w-16 h-16 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
          </svg>
          <p className="text-sm mb-2">{preview.asset.name}</p>
          <audio src={preview.url} controls autoPlay className="w-64" />
        </div>
      )}

      {activeClips.length === 0 && !(preview.url && preview.asset?.type === 'audio') && (
        <div className="absolute inset-0 bg-black cursor-default" style={{ zIndex: 0 }} onClick={onDeselect}>
          <div className="absolute bottom-2 right-2 text-gray-600 text-xs font-mono pointer-events-none">
            {Math.floor(currentTime / 60000)}:
            {Math.floor((currentTime % 60000) / 1000).toString().padStart(2, '0')}
            .{Math.floor((currentTime % 1000) / 10).toString().padStart(2, '0')}
          </div>
        </div>
      )}

      {snapGuides.map((guide, index) => (
        <div
          key={`snap-guide-${index}`}
          className="absolute pointer-events-none"
          style={{
            ...(guide.type === 'x'
              ? { left: guide.position, top: 0, width: 0, height: '100%', borderLeft: '1px dashed rgba(255,100,100,0.8)' }
              : { left: 0, top: guide.position, width: '100%', height: 0, borderTop: '1px dashed rgba(255,100,100,0.8)' }),
            zIndex: 2000,
          }}
        />
      ))}
    </div>
  )
}
