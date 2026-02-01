import React from 'react'

import type { Clip, ClipGroup, Layer } from '@/store/projectStore'
import type { DragState, VideoDragState } from './types'

import ImageClipThumbnails from '../ImageClipThumbnails'
import ShapeSVGRenderer from '../ShapeSVGRenderer'
import VideoClipThumbnails from '../VideoClipThumbnails'

interface VideoLayersProps {
  layers: Layer[]
  projectId: string
  assets: Array<{
    id: string
    name: string
    type: string
    subtype?: string
    storage_url: string
    duration_ms: number | null
    width?: number | null
    height?: number | null
    chroma_key_color?: string | null
  }>
  pixelsPerSecond: number
  getLayerColor: (layer: Layer, index: number) => string
  selectedLayerId: string | null
  selectedVideoClip: { layerId: string; clipId: string } | null
  selectedVideoClips: Set<string>
  selectedAudioGroupId: string | null
  dragState: DragState | null
  videoDragState: VideoDragState | null
  dragGroupVideoClipIds: Set<string>
  dragGroupAudioClipIds: Set<string>
  videoDragGroupVideoClipIds: Set<string>
  videoDragGroupAudioClipIds: Set<string>
  videoClipOverlaps: Map<string, Set<string>>
  getClipGroup: (groupId: string | null | undefined) => ClipGroup | undefined
  handleVideoClipSelect: (layerId: string, clipId: string, e?: React.MouseEvent) => void
  handleVideoClipDragStart: (e: React.MouseEvent, layerId: string, clipId: string, type: 'move' | 'trim-start' | 'trim-end') => void
  handleContextMenu: (e: React.MouseEvent, clipId: string, type: 'video', layerId: string) => void
  getClipDisplayName: (clip: Clip) => string
  getLayerHeight: (layerId: string) => number
  handleLayerResizeStart: (e: React.MouseEvent, layerId: string) => void
  dragOverLayer: string | null
  handleLayerDragOver: (e: React.DragEvent, layerId: string) => void
  handleLayerDragLeave: (e: React.DragEvent) => void
  handleLayerDrop: (e: React.DragEvent, layerId: string) => void
  onLayerClick: (layerId: string) => void
  registerLayerRef?: (layerId: string, el: HTMLDivElement | null) => void
  selectedKeyframeIndex?: number | null
  onKeyframeSelect?: (clipId: string, keyframeIndex: number | null) => void
}

function VideoLayers({
  layers,
  projectId,
  assets,
  pixelsPerSecond,
  getLayerColor,
  selectedLayerId,
  selectedVideoClip,
  selectedVideoClips,
  selectedAudioGroupId,
  dragState,
  videoDragState,
  dragGroupVideoClipIds,
  dragGroupAudioClipIds: _dragGroupAudioClipIds,
  videoDragGroupVideoClipIds,
  videoDragGroupAudioClipIds: _videoDragGroupAudioClipIds,
  videoClipOverlaps,
  getClipGroup,
  handleVideoClipSelect,
  handleVideoClipDragStart,
  handleContextMenu,
  getClipDisplayName,
  getLayerHeight,
  handleLayerResizeStart,
  dragOverLayer,
  handleLayerDragOver,
  handleLayerDragLeave,
  handleLayerDrop,
  onLayerClick,
  registerLayerRef,
  selectedKeyframeIndex,
  onKeyframeSelect,
}: VideoLayersProps) {
  return (
    <>
      {layers.map((layer, layerIndex) => {
        const layerColor = getLayerColor(layer, layerIndex)
        const isLayerSelected = selectedLayerId === layer.id

        return (
          <React.Fragment key={layer.id}>
            <div
              ref={(el) => registerLayerRef?.(layer.id, el)}
              className={`border-b border-gray-700 relative transition-colors cursor-pointer ${
                dragOverLayer === layer.id
                  ? 'bg-purple-900/30 border-purple-500'
                  : isLayerSelected
                    ? 'bg-primary-900/30'
                    : 'bg-gray-800/50 hover:bg-gray-700/50'
              } ${layer.locked ? 'opacity-50' : ''}`}
              style={{ height: getLayerHeight(layer.id) }}
              onClick={() => onLayerClick(layer.id)}
              onDragOver={(e) => handleLayerDragOver(e, layer.id)}
              onDragLeave={handleLayerDragLeave}
              onDrop={(e) => handleLayerDrop(e, layer.id)}
            >
              {layer.clips.map((clip) => {
                const isSelected = selectedVideoClip?.layerId === layer.id && selectedVideoClip?.clipId === clip.id
                const isMultiSelected = selectedVideoClips.has(clip.id)
                const isDragging = videoDragState?.clipId === clip.id
                const clipGroup = getClipGroup(clip.group_id)
                const isLinkedHighlight = clip.group_id && clip.group_id === selectedAudioGroupId
                const hasOverlap = videoClipOverlaps.has(clip.id)

                let visualStartMs = clip.start_ms
                let visualDurationMs = clip.duration_ms
                if (isDragging && videoDragState) {
                  const deltaMs = videoDragState.currentDeltaMs
                  if (videoDragState.type === 'move') {
                    visualStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
                  } else if (videoDragState.type === 'trim-start') {
                    if (videoDragState.isVideoAsset) {
                      const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                      let newDurationMs = videoDragState.initialDurationMs - deltaMs
                      newDurationMs = Math.max(100, newDurationMs)
                      let newSpeed = sourceDuration / newDurationMs
                      newSpeed = Math.max(0.2, Math.min(5.0, newSpeed))
                      visualDurationMs = Math.round(sourceDuration / newSpeed)
                      const durationChange = visualDurationMs - videoDragState.initialDurationMs
                      visualStartMs = Math.max(0, videoDragState.initialStartMs - durationChange)
                    } else {
                      const maxTrim = videoDragState.initialDurationMs - 100
                      const minTrim = videoDragState.isResizableClip ? -Infinity : -videoDragState.initialInPointMs
                      const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                      visualStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
                      const effectiveTrim = visualStartMs - videoDragState.initialStartMs
                      visualDurationMs = videoDragState.initialDurationMs - effectiveTrim
                    }
                  } else if (videoDragState.type === 'trim-end') {
                    if (videoDragState.isVideoAsset) {
                      const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                      let newDurationMs = videoDragState.initialDurationMs + deltaMs
                      newDurationMs = Math.max(100, newDurationMs)
                      let newSpeed = sourceDuration / newDurationMs
                      newSpeed = Math.max(0.2, Math.min(5.0, newSpeed))
                      visualDurationMs = Math.round(sourceDuration / newSpeed)
                    } else {
                      const maxDuration = videoDragState.isResizableClip ? Infinity : videoDragState.assetDurationMs - videoDragState.initialInPointMs
                      visualDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
                    }
                  }
                } else if (videoDragState?.type === 'move' && videoDragGroupVideoClipIds.has(clip.id)) {
                  const groupClip = videoDragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip) {
                    visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
                  }
                } else if (dragState?.type === 'move' && dragGroupVideoClipIds.has(clip.id)) {
                  const groupClip = dragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip) {
                    visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
                  }
                }
                const clipWidth = Math.max((visualDurationMs / 1000) * pixelsPerSecond, 40)

                const clipAsset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
                const isImageClip = clipAsset?.type === 'image'

                return (
                  <div
                    key={clip.id}
                    className={`absolute top-1 bottom-1 rounded select-none group ${
                      isSelected ? 'ring-2 ring-white z-10' : ''
                    } ${isMultiSelected ? 'ring-2 ring-blue-400 z-10' : ''} ${isLinkedHighlight ? 'ring-2 ring-green-400 z-10' : ''} ${isDragging ? 'opacity-80' : ''} ${layer.locked ? 'cursor-not-allowed' : ''} ${hasOverlap ? 'ring-2 ring-orange-500/70' : ''}`}
                    style={{
                      left: 0,
                      transform: `translateX(${(visualStartMs / 1000) * pixelsPerSecond}px)`,
                      width: clipWidth,
                      backgroundColor: isImageClip ? 'transparent' : `${layerColor}cc`,
                      borderColor: hasOverlap ? '#f97316' : layerColor,
                      borderWidth: hasOverlap ? 2 : 1,
                      cursor: layer.locked
                        ? 'not-allowed'
                        : videoDragState?.type === 'move'
                          ? 'grabbing'
                          : videoDragState?.type === 'trim-start' || videoDragState?.type === 'trim-end'
                            ? 'ew-resize'
                            : 'grab',
                      willChange: isDragging ? 'transform, width' : 'auto',
                    }}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (!layer.locked) handleVideoClipSelect(layer.id, clip.id, e)
                    }}
                    onMouseDown={(e) => !layer.locked && handleVideoClipDragStart(e, layer.id, clip.id, 'move')}
                    onContextMenu={(e) => !layer.locked && handleContextMenu(e, clip.id, 'video', layer.id)}
                    title={getClipDisplayName(clip)}
                  >
                    {clipGroup && (
                      <div
                        className="absolute top-0 left-0 right-0 h-1 rounded-t"
                        style={{ backgroundColor: clipGroup.color }}
                        title={clipGroup.name}
                      />
                    )}
                    {clip.asset_id && assets.find(a => a.id === clip.asset_id)?.type === 'video' && (
                      <VideoClipThumbnails
                        projectId={projectId}
                        assetId={clip.asset_id}
                        clipWidth={clipWidth}
                        durationMs={clip.duration_ms}
                        inPointMs={clip.in_point_ms}
                        speed={clip.speed}
                        clipHeight={getLayerHeight(layer.id)}
                      />
                    )}
                    {clip.asset_id && (() => {
                      const asset = assets.find(a => a.id === clip.asset_id)
                      if (!asset || asset.type !== 'image' || !asset.storage_url) return null
                      return (
                        <ImageClipThumbnails
                          imageUrl={asset.storage_url}
                          clipWidth={clipWidth}
                          clipHeight={getLayerHeight(layer.id)}
                        />
                      )
                    })()}
                    {clip.shape && (() => {
                      const shape = clip.shape
                      const layerHeight = getLayerHeight(layer.id)
                      const maxHeight = Math.max(24, layerHeight - 6)
                      const maxWidth = Math.max(24, clipWidth - 6)
                      const shapeAspect = shape.width / shape.height
                      let thumbWidth = maxHeight * shapeAspect
                      let thumbHeight = maxHeight
                      if (thumbWidth > maxWidth) {
                        thumbWidth = maxWidth
                        thumbHeight = maxWidth / shapeAspect
                      }
                      return (
                        <div
                          className="absolute pointer-events-none overflow-hidden"
                          style={{ top: 3, left: 3, maxHeight, maxWidth }}
                        >
                          <ShapeSVGRenderer
                            shape={shape}
                            width={thumbWidth}
                            height={thumbHeight}
                            opacity={clip.effects.opacity}
                          />
                        </div>
                      )
                    })()}
                    {!layer.locked && (
                      <>
                        <div
                          className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                          onMouseDown={(e) => {
                            e.stopPropagation()
                            handleVideoClipDragStart(e, layer.id, clip.id, 'trim-start')
                          }}
                        />
                        <div
                          className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                          onMouseDown={(e) => {
                            e.stopPropagation()
                            handleVideoClipDragStart(e, layer.id, clip.id, 'trim-end')
                          }}
                        />
                      </>
                    )}
                    {((clip.effects.fade_in_ms ?? 0) > 0 || (clip.effects.fade_out_ms ?? 0) > 0) && (() => {
                      const fadeInPx = ((clip.effects.fade_in_ms ?? 0) / 1000) * pixelsPerSecond
                      const fadeOutPx = ((clip.effects.fade_out_ms ?? 0) / 1000) * pixelsPerSecond
                      const w = clipWidth
                      const h = 32
                      return (
                        <svg
                          className="absolute inset-0 w-full h-full pointer-events-none z-30"
                          preserveAspectRatio="none"
                          viewBox={`0 0 ${w} ${h}`}
                        >
                          {(clip.effects.fade_in_ms ?? 0) > 0 && (
                            <polygon points={`0,${h} ${fadeInPx},0 0,0`} fill="rgba(0,0,0,0.5)" />
                          )}
                          {(clip.effects.fade_out_ms ?? 0) > 0 && (
                            <polygon points={`${w},${h} ${w - fadeOutPx},0 ${w},0`} fill="rgba(0,0,0,0.5)" />
                          )}
                          <polyline
                            points={`0,${h} ${fadeInPx},2 ${w - fadeOutPx},2 ${w},${h}`}
                            fill="none"
                            stroke="rgba(255,255,255,0.9)"
                            strokeWidth="2"
                            vectorEffect="non-scaling-stroke"
                          />
                        </svg>
                      )
                    })()}
                    {clip.keyframes && clip.keyframes.length > 0 && (
                      <div className="absolute inset-0 pointer-events-none z-40">
                        {clip.keyframes.map((kf, kfIdx) => {
                          const kfPositionPx = (kf.time_ms / 1000) * pixelsPerSecond
                          const isKfSelected = isSelected && selectedKeyframeIndex === kfIdx
                          return (
                            <div
                              key={kfIdx}
                              className="absolute pointer-events-auto cursor-pointer"
                              style={{
                                left: kfPositionPx - 5,
                                top: '50%',
                                marginTop: -5,
                                width: 10,
                                height: 10,
                                transform: 'rotate(45deg)',
                                backgroundColor: isKfSelected ? '#facc15' : '#f59e0b',
                                border: isKfSelected ? '2px solid #fff' : '1px solid rgba(0,0,0,0.3)',
                                borderRadius: 1,
                                zIndex: 50,
                              }}
                              title={`キーフレーム ${kfIdx + 1} (${(kf.time_ms / 1000).toFixed(2)}s)`}
                              onClick={(e) => {
                                e.stopPropagation()
                                onKeyframeSelect?.(clip.id, isKfSelected ? null : kfIdx)
                              }}
                            />
                          )
                        })}
                      </div>
                    )}
                    <span className={`absolute bottom-1 left-0 right-0 text-xs text-white px-2 truncate pointer-events-none transition-opacity z-50 ${clip.text_content ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
                      {getClipDisplayName(clip)}
                    </span>
                  </div>
                )
              })}
              <div
                className="absolute bottom-0 left-0 right-0 h-1 cursor-ns-resize hover:bg-primary-500/50 transition-colors z-10"
                onMouseDown={(e) => handleLayerResizeStart(e, layer.id)}
                title="ドラッグして高さを変更"
              />
            </div>
          </React.Fragment>
        )
      })}
    </>
  )
}

export default VideoLayers
