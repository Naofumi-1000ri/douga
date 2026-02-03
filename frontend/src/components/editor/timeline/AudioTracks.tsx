import React from 'react'

import type { ClipGroup, AudioTrack } from '@/store/projectStore'
import type { DragState, VideoDragState } from './types'

import AudioClipWaveform from './AudioClipWaveform'
import VolumeEnvelope from '../VolumeEnvelope'

interface AudioTracksProps {
  tracks: AudioTrack[]
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
  projectId: string
  pixelsPerSecond: number
  selectedClip: { trackId: string; clipId: string } | null
  selectedAudioClips: Set<string>
  dragState: DragState | null
  videoDragState: VideoDragState | null
  dragGroupAudioClipIds: Set<string>
  videoDragGroupAudioClipIds: Set<string>
  audioClipOverlaps: Map<string, Set<string>>
  getClipGroup: (groupId: string | null | undefined) => ClipGroup | undefined
  handleClipSelect: (trackId: string, clipId: string, e?: React.MouseEvent) => void
  handleClipDragStart: (e: React.MouseEvent, trackId: string, clipId: string, type: 'move' | 'trim-start' | 'trim-end') => void
  handleContextMenu: (e: React.MouseEvent, clipId: string, type: 'audio', layerId?: string, trackId?: string) => void
  handleVolumeKeyframeAdd: (trackId: string, clipId: string, timeMs: number, value: number) => void
  handleVolumeKeyframeUpdate: (trackId: string, clipId: string, index: number, timeMs: number, value: number) => void
  handleVolumeKeyframeRemove: (trackId: string, clipId: string, index: number) => void
  getAssetName: (assetId: string) => string
  dragOverTrack: string | null
  handleDragOver: (e: React.DragEvent, trackId: string) => void
  handleDragLeave: (e: React.DragEvent) => void
  handleDrop: (e: React.DragEvent, trackId: string) => void
  registerTrackRef?: (trackId: string, el: HTMLDivElement | null) => void
  onTrackClick?: (trackId: string) => void
}

function AudioTracks({
  tracks,
  assets,
  projectId,
  pixelsPerSecond,
  selectedClip,
  selectedAudioClips,
  dragState,
  videoDragState,
  dragGroupAudioClipIds,
  videoDragGroupAudioClipIds,
  audioClipOverlaps,
  getClipGroup,
  handleClipSelect,
  handleClipDragStart,
  handleContextMenu,
  handleVolumeKeyframeAdd,
  handleVolumeKeyframeUpdate,
  handleVolumeKeyframeRemove,
  getAssetName,
  dragOverTrack,
  handleDragOver,
  handleDragLeave,
  handleDrop,
  registerTrackRef,
  onTrackClick,
}: AudioTracksProps) {
  return (
    <>
      {tracks.map((track) => (
        <div
          key={track.id}
          ref={(el) => registerTrackRef?.(track.id, el)}
          className={`h-16 border-b border-gray-700 relative transition-colors ${
            dragOverTrack === track.id
              ? 'bg-green-900/30 border-green-500'
              : 'bg-gray-800/50'
          }`}
          onClick={() => onTrackClick?.(track.id)}
          onDragOver={(e) => handleDragOver(e, track.id)}
          onDragLeave={handleDragLeave}
          onDrop={(e) => handleDrop(e, track.id)}
        >
          {track.clips.map((clip) => {
            const isSelected = selectedClip?.trackId === track.id && selectedClip?.clipId === clip.id
            const isMultiSelected = selectedAudioClips.has(clip.id)
            const isDragging = dragState?.clipId === clip.id
            const clipColor = track.type === 'narration' ? '#22c55e' : track.type === 'bgm' ? '#3b82f6' : '#f59e0b'
            const audioClipGroup = getClipGroup(clip.group_id)
            const hasAudioOverlap = audioClipOverlaps.has(clip.id)

            let visualStartMs = clip.start_ms
            let visualDurationMs = clip.duration_ms
            let visualInPointMs = clip.in_point_ms
            if (isDragging && dragState) {
              const deltaMs = dragState.currentDeltaMs
              if (dragState.type === 'move') {
                visualStartMs = Math.max(0, dragState.initialStartMs + deltaMs)
              } else if (dragState.type === 'trim-start') {
                const maxTrim = dragState.initialDurationMs - 100
                const minTrim = -dragState.initialInPointMs
                const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                visualStartMs = Math.max(0, dragState.initialStartMs + trimAmount)
                const effectiveTrim = visualStartMs - dragState.initialStartMs
                visualDurationMs = dragState.initialDurationMs - effectiveTrim
                visualInPointMs = dragState.initialInPointMs + effectiveTrim
              } else if (dragState.type === 'trim-end') {
                const maxDuration = dragState.assetDurationMs - dragState.initialInPointMs
                visualDurationMs = Math.min(Math.max(100, dragState.initialDurationMs + deltaMs), maxDuration)
              }
            } else if (dragState?.type === 'move' && dragGroupAudioClipIds.has(clip.id)) {
              const groupClip = dragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
              if (groupClip) {
                visualStartMs = Math.max(0, groupClip.initialStartMs + dragState.currentDeltaMs)
              }
            } else if (videoDragState?.type === 'move' && videoDragGroupAudioClipIds.has(clip.id)) {
              const groupClip = videoDragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
              if (groupClip) {
                visualStartMs = Math.max(0, groupClip.initialStartMs + videoDragState.currentDeltaMs)
              }
            } else if (videoDragState?.type === 'trim-start' && videoDragGroupAudioClipIds.has(clip.id)) {
              // Group audio clip trim-start preview (when video clip is being trimmed)
              const groupClip = videoDragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
              if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                const deltaMs = videoDragState.currentDeltaMs
                const maxTrim = groupClip.initialDurationMs - 100
                const minTrim = -groupClip.initialInPointMs
                const trimAmount = Math.min(Math.max(minTrim, deltaMs), maxTrim)
                visualStartMs = Math.max(0, groupClip.initialStartMs + trimAmount)
                const effectiveTrim = visualStartMs - groupClip.initialStartMs
                visualDurationMs = groupClip.initialDurationMs - effectiveTrim
                visualInPointMs = groupClip.initialInPointMs + effectiveTrim
              }
            } else if (videoDragState?.type === 'trim-end' && videoDragGroupAudioClipIds.has(clip.id)) {
              // Group audio clip trim-end preview (when video clip is being trimmed)
              const groupClip = videoDragState.groupAudioClips?.find(gc => gc.clipId === clip.id)
              if (groupClip && groupClip.initialDurationMs !== undefined && groupClip.initialInPointMs !== undefined) {
                const deltaMs = videoDragState.currentDeltaMs
                const maxDuration = (groupClip.assetDurationMs ?? Infinity) - groupClip.initialInPointMs
                visualDurationMs = Math.min(Math.max(100, groupClip.initialDurationMs + deltaMs), maxDuration)
              }
            }
            const clipWidth = Math.max((visualDurationMs / 1000) * pixelsPerSecond, 40)

            // Determine box-shadow based on selection state
            const selectionShadow = (isSelected || isMultiSelected)
              ? 'inset 0 0 0 3px #ffffff'
              : hasAudioOverlap
                ? 'inset 0 0 0 2px #f97316'
                : `inset 0 0 0 1px ${clipColor}`

            return (
              <div
                key={clip.id}
                className={`absolute top-1 bottom-1 rounded select-none group ${
                  (isSelected || isMultiSelected) ? 'z-10' : ''
                } ${isDragging ? 'opacity-80' : ''} ${hasAudioOverlap ? 'z-10' : ''}`}
                style={{
                  left: (visualStartMs / 1000) * pixelsPerSecond,
                  width: clipWidth,
                  backgroundColor: `${clipColor}33`,
                  cursor: dragState?.type === 'move' ? 'grabbing' : 'grab',
                  willChange: isDragging ? 'left, width' : 'auto',
                }}
                onClick={(e) => {
                  e.stopPropagation()
                  handleClipSelect(track.id, clip.id, e)
                }}
                onMouseDown={(e) => {
                  handleClipSelect(track.id, clip.id, e)
                  handleClipDragStart(e, track.id, clip.id, 'move')
                }}
                onContextMenu={(e) => handleContextMenu(e, clip.id, 'audio', undefined, track.id)}
              >
                {audioClipGroup && (
                  <div
                    className="absolute top-0 left-0 right-0 h-1 rounded-t"
                    style={{ backgroundColor: audioClipGroup.color }}
                    title={audioClipGroup.name}
                  />
                )}
                <AudioClipWaveform
                  projectId={projectId}
                  assetId={clip.asset_id}
                  width={clipWidth}
                  height={56}
                  color={clipColor}
                  inPointMs={visualInPointMs}
                  clipDurationMs={visualDurationMs}
                  assetDurationMs={assets.find(a => a.id === clip.asset_id)?.duration_ms || clip.duration_ms}
                />
                <div
                  className="absolute left-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                  onMouseDown={(e) => {
                    e.stopPropagation()
                    handleClipDragStart(e, track.id, clip.id, 'trim-start')
                  }}
                />
                <div
                  className="absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize hover:bg-white/30 z-20"
                  onMouseDown={(e) => {
                    e.stopPropagation()
                    handleClipDragStart(e, track.id, clip.id, 'trim-end')
                  }}
                />
                {(clip.fade_in_ms > 0 || clip.fade_out_ms > 0) && (() => {
                  const fadeInPx = (clip.fade_in_ms / 1000) * pixelsPerSecond
                  const fadeOutPx = (clip.fade_out_ms / 1000) * pixelsPerSecond
                  const w = clipWidth
                  const h = 48
                  return (
                    <svg
                      className="absolute inset-0 w-full h-full pointer-events-none z-30"
                      preserveAspectRatio="none"
                      viewBox={`0 0 ${w} ${h}`}
                    >
                      {clip.fade_in_ms > 0 && (
                        <polygon points={`0,${h} ${fadeInPx},0 0,0`} fill="rgba(0,0,0,0.5)" />
                      )}
                      {clip.fade_out_ms > 0 && (
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
                {clip.volume_keyframes && clip.volume_keyframes.length > 0 && (
                  <VolumeEnvelope
                    keyframes={clip.volume_keyframes}
                    durationMs={visualDurationMs}
                    width={clipWidth}
                    height={48}
                    onKeyframeAdd={(timeMs, value) => handleVolumeKeyframeAdd(track.id, clip.id, timeMs, value)}
                    onKeyframeUpdate={(index, timeMs, value) => handleVolumeKeyframeUpdate(track.id, clip.id, index, timeMs, value)}
                    onKeyframeRemove={(index) => handleVolumeKeyframeRemove(track.id, clip.id, index)}
                  />
                )}
                <span className="text-xs text-white px-3 truncate block leading-[3.5rem] pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity">
                  {getAssetName(clip.asset_id)}
                </span>
                {clip.group_id && (
                  <div className="absolute top-0.5 right-1 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity" title="グループ化済み">
                    <svg className="w-3 h-3 text-white/70" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
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
          {dragOverTrack === track.id && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <span className="text-green-400 text-sm">ここにドロップ</span>
            </div>
          )}
        </div>
      ))}
    </>
  )
}

export default AudioTracks
