import React, { useCallback, useEffect, useState } from 'react'

import type { Clip, ClipGroup, Layer } from '@/store/projectStore'
import type { CrossLayerDropPreview, DragState, VideoDragState } from './types'

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
  handleVideoClipDoubleClick: (layerId: string, clipId: string) => void
  handleVideoClipDragStart: (e: React.MouseEvent, layerId: string, clipId: string, type: 'move' | 'trim-start' | 'trim-end' | 'stretch-start' | 'stretch-end' | 'freeze-end') => void
  handleContextMenu: (e: React.MouseEvent, clipId: string, type: 'video', layerId: string) => void
  stretchModeClips?: Set<string>  // Clips with stretch mode enabled (orange handles)
  onSetStretchMode?: (clipId: string, enabled: boolean) => void  // Set stretch mode for a clip
  freezeModeClips?: Set<string>  // Clips with freeze-end mode enabled (blue handles)
  onSetFreezeMode?: (clipId: string, enabled: boolean) => void  // Set freeze-end mode for a clip
  getClipDisplayName: (clip: Clip) => string
  getLayerHeight: (layerId: string) => number
  handleLayerResizeStart: (e: React.MouseEvent, layerId: string) => void
  dragOverLayer: string | null
  dropPreview: {
    layerId: string
    timeMs: number
    durationMs: number
  } | null
  handleLayerDragOver: (e: React.DragEvent, layerId: string) => void
  handleLayerDragLeave: (e: React.DragEvent) => void
  handleLayerDrop: (e: React.DragEvent, layerId: string) => void
  onLayerClick: (layerId: string) => void
  registerLayerRef?: (layerId: string, el: HTMLDivElement | null) => void
  selectedKeyframeIndex?: number | null
  onKeyframeSelect?: (clipId: string, keyframeIndex: number | null) => void
  unmappedAssetIds?: Set<string>  // Asset IDs that couldn't be mapped from session
  crossLayerDragTargetId?: string | null  // Layer ID that is the target of cross-layer drag
  crossLayerDropPreview?: CrossLayerDropPreview | null  // Drop preview for cross-layer drag
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
  handleVideoClipDoubleClick,
  handleVideoClipDragStart,
  handleContextMenu,
  getClipDisplayName,
  getLayerHeight,
  handleLayerResizeStart,
  dragOverLayer,
  dropPreview,
  handleLayerDragOver,
  handleLayerDragLeave,
  handleLayerDrop,
  onLayerClick,
  registerLayerRef,
  selectedKeyframeIndex,
  onKeyframeSelect,
  unmappedAssetIds = new Set(),
  crossLayerDragTargetId,
  crossLayerDropPreview,
  stretchModeClips = new Set(),
  onSetStretchMode,
  freezeModeClips = new Set(),
  onSetFreezeMode,
}: VideoLayersProps) {
  const [resizeMenu, setResizeMenu] = useState<{ clipId: string; x: number; y: number } | null>(null)

  // Close resize mode menu on outside click
  useEffect(() => {
    if (!resizeMenu) return
    const handleClick = () => setResizeMenu(null)
    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }, [resizeMenu])

  const handleResizeHandleContextMenu = useCallback((e: React.MouseEvent, clipId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setResizeMenu({ clipId, x: e.clientX, y: e.clientY })
  }, [])

  return (
    <>
      {layers.map((layer, layerIndex) => {
        const layerColor = getLayerColor(layer, layerIndex)
        const isLayerSelected = selectedLayerId === layer.id
        const isCrossLayerDragTarget = crossLayerDragTargetId === layer.id

        return (
          <React.Fragment key={layer.id}>
            <div
              ref={(el) => registerLayerRef?.(layer.id, el)}
              className={`border-b border-gray-700 relative z-[1] transition-colors cursor-pointer ${
                isCrossLayerDragTarget
                  ? 'bg-emerald-900/40 border-emerald-500'
                  : dragOverLayer === layer.id
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
                let visualInPointMs = clip.in_point_ms

                // Debug: Check if this clip should be part of the group drag
                if (videoDragState?.type === 'move' && videoDragGroupVideoClipIds.size > 0) {
                  console.log('[VideoLayers] clip:', clip.id, 'isDragging:', isDragging, 'inGroupIds:', videoDragGroupVideoClipIds.has(clip.id), 'groupIds:', [...videoDragGroupVideoClipIds])
                }

                if (isDragging && videoDragState) {
                  const deltaMs = videoDragState.currentDeltaMs
                  if (videoDragState.type === 'move') {
                    visualStartMs = Math.max(0, videoDragState.initialStartMs + deltaMs)
                    // Debug: log if there's a mismatch between clip.start_ms and initialStartMs
                    if (Math.abs(clip.start_ms - videoDragState.initialStartMs) > 1) {
                      console.warn('[VideoLayers] start_ms mismatch! clip.start_ms:', clip.start_ms, 'initialStartMs:', videoDragState.initialStartMs)
                    }
                  } else if (videoDragState.type === 'trim-start') {
                    // Crop mode: adjust in_point and duration
                    const maxTrim = videoDragState.initialDurationMs - 100
                    const speed = clip.speed ?? 1
                    const minTrim = videoDragState.isResizableClip ? -Infinity : Math.ceil(-(videoDragState.initialInPointMs / speed))
                    const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                    visualStartMs = Math.max(0, videoDragState.initialStartMs + trimAmount)
                    const effectiveTrim = visualStartMs - videoDragState.initialStartMs
                    visualDurationMs = videoDragState.initialDurationMs - effectiveTrim
                    const sourceTrimMs = videoDragState.isResizableClip ? effectiveTrim : Math.round(effectiveTrim * speed)
                    visualInPointMs = videoDragState.isResizableClip ? 0 : videoDragState.initialInPointMs + sourceTrimMs
                  } else if (videoDragState.type === 'trim-end') {
                    // Crop mode: adjust out_point and duration
                    const speed = clip.speed ?? 1
                    const maxDuration = videoDragState.isResizableClip
                      ? Infinity
                      : Math.floor((videoDragState.assetDurationMs - videoDragState.initialInPointMs) / speed)
                    visualDurationMs = Math.min(Math.max(100, videoDragState.initialDurationMs + deltaMs), maxDuration)
                  } else if (videoDragState.type === 'stretch-start') {
                    // Stretch mode: adjust speed from start
                    const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                    let newDurationMs = videoDragState.initialDurationMs - deltaMs
                    newDurationMs = Math.max(100, newDurationMs)
                    let newSpeed = sourceDuration / newDurationMs
                    newSpeed = Math.max(0.2, Math.min(5.0, newSpeed))
                    visualDurationMs = Math.round(sourceDuration / newSpeed)
                    const durationChange = visualDurationMs - videoDragState.initialDurationMs
                    visualStartMs = Math.max(0, videoDragState.initialStartMs - durationChange)
                  } else if (videoDragState.type === 'stretch-end') {
                    // Stretch mode: adjust speed from end
                    const sourceDuration = videoDragState.initialOutPointMs - videoDragState.initialInPointMs
                    let newDurationMs = videoDragState.initialDurationMs + deltaMs
                    newDurationMs = Math.max(100, newDurationMs)
                    let newSpeed = sourceDuration / newDurationMs
                    newSpeed = Math.max(0.2, Math.min(5.0, newSpeed))
                    visualDurationMs = Math.round(sourceDuration / newSpeed)
                  }
                } else if (videoDragState?.type === 'move' && videoDragGroupVideoClipIds.has(clip.id)) {
                  const groupClip = videoDragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip) {
                    visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
                  }
                } else if (videoDragState?.type === 'trim-start' && videoDragGroupVideoClipIds.has(clip.id)) {
                  // Group clip trim-start preview
                  const groupClip = videoDragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                    const deltaMs = videoDragState.currentDeltaMs
                    const speed = clip.speed ?? 1
                    const maxTrim = groupClip.initialDurationMs - 100
                    const minTrim = Math.ceil(-(groupClip.initialInPointMs / speed))
                    const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                    visualStartMs = Math.max(0, groupClip.initialStartMs + trimAmount)
                    const effectiveTrim = visualStartMs - groupClip.initialStartMs
                    visualDurationMs = groupClip.initialDurationMs - effectiveTrim
                    const sourceTrimMs = Math.round(effectiveTrim * speed)
                    visualInPointMs = groupClip.initialInPointMs + sourceTrimMs
                  }
                } else if (videoDragState?.type === 'trim-end' && videoDragGroupVideoClipIds.has(clip.id)) {
                  // Group clip trim-end preview
                  const groupClip = videoDragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                    const deltaMs = videoDragState.currentDeltaMs
                    const speed = clip.speed ?? 1
                    const maxDuration = Math.floor(((groupClip.assetDurationMs ?? Infinity) - groupClip.initialInPointMs) / speed)
                    visualDurationMs = Math.min(Math.max(100, groupClip.initialDurationMs + deltaMs), maxDuration)
                  }
                } else if (dragState?.type === 'move' && dragGroupVideoClipIds.has(clip.id)) {
                  const groupClip = dragState.groupVideoClips?.find(gc => gc.clipId === clip.id)
                  if (groupClip) {
                    visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
                  }
                }
                let freezeMs = clip.freeze_frame_ms ?? 0
                // freeze-end drag preview
                if (videoDragState?.type === 'freeze-end' && clip.id === videoDragState.clipId) {
                  const deltaMs = videoDragState.currentDeltaMs ?? 0
                  freezeMs = Math.max(0, (videoDragState.initialFreezeFrameMs ?? 0) + deltaMs)
                }
                const effectiveDurationMs = visualDurationMs + freezeMs
                const clipWidth = Math.max((effectiveDurationMs / 1000) * pixelsPerSecond, 2)

                const clipAsset = clip.asset_id ? assets.find(a => a.id === clip.asset_id) : null
                const isImageClip = clipAsset?.type === 'image'

                // Determine box-shadow based on selection state
                const selectionShadow = (isSelected || isMultiSelected)
                  ? 'inset 0 0 0 3px #ffffff'
                  : isLinkedHighlight
                    ? 'inset 0 0 0 2px #4ade80'
                    : hasOverlap
                      ? 'inset 0 0 0 2px #f97316'
                      : `inset 0 0 0 1px ${layerColor}`

                return (
                  <div
                    key={clip.id}
                    className={`absolute top-1 bottom-1 rounded select-none group overflow-hidden ${
                      (isSelected || isMultiSelected) ? 'z-10' : ''
                    } ${isLinkedHighlight ? 'z-10' : ''} ${isDragging ? 'opacity-80' : ''} ${layer.locked ? 'cursor-not-allowed' : ''} ${hasOverlap ? 'z-10' : ''}`}
                    style={{
                      left: (visualStartMs / 1000) * pixelsPerSecond,
                      width: clipWidth,
                      backgroundColor: isImageClip ? 'transparent' : `${layerColor}cc`,
                      cursor: layer.locked
                        ? 'not-allowed'
                        : videoDragState?.type === 'move'
                          ? 'grabbing'
                          : videoDragState?.type === 'trim-start' || videoDragState?.type === 'trim-end'
                            ? 'ew-resize'
                            : 'grab',
                      willChange: isDragging ? 'left, width' : 'auto',
                    }}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (!layer.locked) handleVideoClipSelect(layer.id, clip.id, e)
                    }}
                    onDoubleClick={(e) => {
                      e.stopPropagation()
                      if (!layer.locked) handleVideoClipDoubleClick(layer.id, clip.id)
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
                        inPointMs={visualInPointMs}
                        durationMs={visualDurationMs}
                        speed={clip.speed ?? 1}
                        clipHeight={getLayerHeight(layer.id) - 8}
                      />
                    )}
                    {clip.asset_id && (() => {
                      const asset = assets.find(a => a.id === clip.asset_id)
                      if (!asset || asset.type !== 'image' || !asset.storage_url) return null
                      return (
                        <ImageClipThumbnails
                          imageUrl={asset.storage_url}
                          clipWidth={clipWidth}
                          clipHeight={getLayerHeight(layer.id) - 8}
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
                    {!layer.locked && clipWidth > 24 && (() => {
                      const isStretchMode = stretchModeClips.has(clip.id)
                      const isFreezeMode = freezeModeClips.has(clip.id)
                      const leftHandleColor = isStretchMode ? 'bg-orange-500/50 hover:bg-orange-500/70' : 'hover:bg-white/30'
                      const rightHandleColor = isFreezeMode
                        ? 'bg-blue-500/50 hover:bg-blue-500/70'
                        : isStretchMode
                          ? 'bg-orange-500/50 hover:bg-orange-500/70'
                          : 'hover:bg-white/30'
                      // Dynamic handle width: max 12px, but no more than 20% of clip width
                      const handleWidth = Math.max(4, Math.min(12, clipWidth * 0.2))
                      return (
                        <>
                          <div
                            className={`absolute left-0 top-0 bottom-0 cursor-ew-resize z-20 ${leftHandleColor}`}
                            style={{ width: handleWidth }}
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleVideoClipDragStart(e, layer.id, clip.id, isStretchMode ? 'stretch-start' : 'trim-start')
                            }}
                            onContextMenu={(e) => handleResizeHandleContextMenu(e, clip.id)}
                            title={isStretchMode ? '伸縮モード (右クリックで変更)' : 'Cropモード (右クリックで変更)'}
                          />
                          <div
                            className={`absolute right-0 top-0 bottom-0 cursor-ew-resize z-20 ${rightHandleColor}`}
                            style={{ width: handleWidth }}
                            onMouseDown={(e) => {
                              e.stopPropagation()
                              handleVideoClipDragStart(e, layer.id, clip.id, isFreezeMode ? 'freeze-end' : isStretchMode ? 'stretch-end' : 'trim-end')
                            }}
                            onContextMenu={(e) => handleResizeHandleContextMenu(e, clip.id)}
                            title={isFreezeMode ? '静止画延長モード (右クリックで変更)' : isStretchMode ? '伸縮モード (右クリックで変更)' : 'Cropモード (右クリックで変更)'}
                          />
                        </>
                      )
                    })()}
                    {freezeMs > 0 && (() => {
                      const freezeWidthPx = (freezeMs / 1000) * pixelsPerSecond
                      return (
                        <div
                          className="absolute top-0 bottom-0 pointer-events-none"
                          style={{
                            zIndex: 25,
                            right: 0,
                            width: Math.min(freezeWidthPx, clipWidth),
                            background: 'repeating-linear-gradient(-45deg, transparent, transparent 3px, rgba(59,130,246,0.3) 3px, rgba(59,130,246,0.3) 6px)',
                            borderLeft: '1px dashed rgba(59,130,246,0.6)',
                          }}
                        >
                          {freezeWidthPx > 30 && (
                            <span className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-[10px] text-blue-300 whitespace-nowrap font-medium">
                              {(freezeMs / 1000).toFixed(1)}s
                            </span>
                          )}
                        </div>
                      )
                    })()}
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
                              key={`kf-${clip.id}-${kf.time_ms}`}
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
                    {/* Warning icon for unmapped assets */}
                    {clip.asset_id && unmappedAssetIds.has(clip.asset_id) && (
                      <div
                        className="absolute top-1 right-1 text-orange-400 z-50"
                        title="アセットが見つかりません"
                      >
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
                        </svg>
                      </div>
                    )}
                    {/* Border overlay - always on top */}
                    <div
                      className="absolute inset-0 rounded pointer-events-none z-[60]"
                      style={{ boxShadow: selectionShadow }}
                    />
                  </div>
                )
              })}
              {/* Drop preview indicator for asset drag from library */}
              {dropPreview && dropPreview.layerId === layer.id && (
                <div
                  className="absolute top-1 bottom-1 rounded pointer-events-none z-50"
                  style={{
                    left: (dropPreview.timeMs / 1000) * pixelsPerSecond,
                    width: Math.max((dropPreview.durationMs / 1000) * pixelsPerSecond, 40),
                    backgroundColor: 'rgba(255, 255, 255, 0.2)',
                    boxShadow: 'inset 0 0 0 3px rgba(255, 255, 255, 0.8)',
                  }}
                >
                  {/* Vertical line at drop position */}
                  <div className="absolute left-0 top-0 bottom-0 w-1 bg-white rounded-l" />
                </div>
              )}
              {/* Cross-layer drop preview indicator for clip drag between layers */}
              {crossLayerDropPreview && crossLayerDropPreview.layerId === layer.id && (
                <div
                  className="absolute top-1 bottom-1 rounded pointer-events-none z-50"
                  style={{
                    left: (crossLayerDropPreview.timeMs / 1000) * pixelsPerSecond,
                    width: Math.max((crossLayerDropPreview.durationMs / 1000) * pixelsPerSecond, 40),
                    backgroundColor: 'rgba(16, 185, 129, 0.3)',
                    boxShadow: 'inset 0 0 0 3px rgba(16, 185, 129, 0.9)',
                  }}
                >
                  {/* Vertical line at drop position */}
                  <div className="absolute left-0 top-0 bottom-0 w-1 bg-emerald-500 rounded-l" />
                </div>
              )}
              <div
                className="absolute bottom-0 left-0 right-0 h-1 cursor-ns-resize hover:bg-primary-500/50 transition-colors z-10"
                onMouseDown={(e) => handleLayerResizeStart(e, layer.id)}
                title="ドラッグして高さを変更"
              />
            </div>
          </React.Fragment>
        )
      })}
      {/* Resize mode context menu */}
      {resizeMenu && (
        <div
          className="fixed bg-gray-800 rounded-lg shadow-xl border border-gray-600 z-[9999] py-1 min-w-[160px]"
          style={{ left: resizeMenu.x, top: resizeMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-[10px] text-gray-500 uppercase tracking-wider">端のドラッグ動作</div>
          <button
            onClick={() => {
              onSetStretchMode?.(resizeMenu.clipId, false)
              onSetFreezeMode?.(resizeMenu.clipId, false)
              setResizeMenu(null)
            }}
            className={`w-full px-3 py-1.5 text-xs text-left flex items-center gap-2 transition-colors ${
              !stretchModeClips.has(resizeMenu.clipId) && !freezeModeClips.has(resizeMenu.clipId)
                ? 'text-white bg-gray-600/50'
                : 'text-gray-300 hover:bg-gray-700'
            }`}
          >
            <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div>
              <div>Cropモード</div>
              <div className="text-[10px] text-gray-500">端をトリミング（素材の長さ内）</div>
            </div>
            {!stretchModeClips.has(resizeMenu.clipId) && !freezeModeClips.has(resizeMenu.clipId) && (
              <svg className="w-3 h-3 ml-auto text-emerald-400 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </button>
          <button
            onClick={() => {
              onSetStretchMode?.(resizeMenu.clipId, true)
              onSetFreezeMode?.(resizeMenu.clipId, false)
              setResizeMenu(null)
            }}
            className={`w-full px-3 py-1.5 text-xs text-left flex items-center gap-2 transition-colors ${
              stretchModeClips.has(resizeMenu.clipId)
                ? 'text-white bg-orange-600/30'
                : 'text-gray-300 hover:bg-gray-700'
            }`}
          >
            <svg className="w-4 h-4 flex-shrink-0 text-orange-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
            </svg>
            <div>
              <div>伸縮モード</div>
              <div className="text-[10px] text-gray-500">速度を変えて伸縮（制限なし）</div>
            </div>
            {stretchModeClips.has(resizeMenu.clipId) && (
              <svg className="w-3 h-3 ml-auto text-orange-400 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </button>
          <button
            onClick={() => {
              if (freezeModeClips.has(resizeMenu.clipId)) {
                onSetFreezeMode?.(resizeMenu.clipId, false)
              } else {
                onSetFreezeMode?.(resizeMenu.clipId, true)
                onSetStretchMode?.(resizeMenu.clipId, false)
              }
              setResizeMenu(null)
            }}
            className={`w-full px-3 py-1.5 text-xs text-left flex items-center gap-2 transition-colors ${
              freezeModeClips.has(resizeMenu.clipId)
                ? 'text-white bg-blue-600/30'
                : 'text-gray-300 hover:bg-gray-700'
            }`}
          >
            <span className="w-4 h-4 flex items-center justify-center text-blue-400">⏸</span>
            <div>
              <div>静止画で延長</div>
              <div className="text-[10px] text-gray-500">末尾に静止画を追加（右端のみ）</div>
            </div>
            {freezeModeClips.has(resizeMenu.clipId) && (
              <svg className="w-3 h-3 ml-auto text-blue-400 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </button>
        </div>
      )}
    </>
  )
}

export default React.memo(VideoLayers)
