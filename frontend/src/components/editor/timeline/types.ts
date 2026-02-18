export type ContextMenuType = 'video' | 'audio'

export interface TimelineContextMenuState {
  x: number
  y: number
  clipId: string
  layerId?: string  // For video clips
  trackId?: string  // For audio clips
  type: ContextMenuType
  overlappingClips?: Array<{ clipId: string; name: string }>
}

// Track header context menu state (for layer/audio track visibility toggle)
export type TrackHeaderContextMenuType = 'layer' | 'audio_track'

export interface TrackHeaderContextMenuState {
  x: number
  y: number
  id: string  // Layer ID or AudioTrack ID
  type: TrackHeaderContextMenuType
  isVisible: boolean  // Current visibility state
  name: string  // Track/Layer name for display
}

export interface GroupClipInitialPosition {
  clipId: string
  layerOrTrackId: string
  initialStartMs: number
  // Additional properties for group crop operations
  initialDurationMs?: number
  initialInPointMs?: number
  initialOutPointMs?: number
  assetDurationMs?: number  // For constraining trim
}

export interface DragState {
  type: 'move' | 'trim-start' | 'trim-end' | 'stretch-start' | 'stretch-end'
  trackId: string
  clipId: string
  startX: number
  startY: number  // Added for cross-track drag detection
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  assetDurationMs: number
  currentDeltaMs: number
  // Offset in ms from the clip's left edge to where the mouse clicked
  // Used to keep the ghost aligned with the mouse cursor during drag
  clickOffsetMs: number
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
  targetTrackId?: string | null  // Track to drop the clip onto (for cross-track drag)
}

export interface VideoDragState {
  type: 'move' | 'trim-start' | 'trim-end' | 'stretch-start' | 'stretch-end' | 'freeze-end'
  layerId: string
  clipId: string
  startX: number
  startY: number  // Added for cross-layer drag detection
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  initialOutPointMs: number
  initialSpeed: number
  assetDurationMs: number
  currentDeltaMs: number
  // Offset in ms from the clip's left edge to where the mouse clicked
  // Used to keep the ghost aligned with the mouse cursor during drag
  clickOffsetMs: number
  isResizableClip: boolean
  isVideoAsset: boolean
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
  targetLayerId?: string | null  // Layer to drop the clip onto (for cross-layer drag)
  initialFreezeFrameMs?: number  // For freeze-end drag: initial freeze_frame_ms value
}

// Cross-layer drop preview state
export interface CrossLayerDropPreview {
  layerId: string
  timeMs: number
  durationMs: number
}

// Cross-track drop preview state (for audio clips)
export interface CrossTrackDropPreview {
  trackId: string
  timeMs: number
  durationMs: number
}

// Clipboard state for copy/paste
export interface ClipboardAudioClip {
  clipData: {
    asset_id: string
    start_ms: number
    duration_ms: number
    in_point_ms: number
    out_point_ms: number | null
    volume: number
    fade_in_ms: number
    fade_out_ms: number
    group_id?: string | null
    volume_keyframes?: Array<{ time_ms: number; value: number }>
  }
  sourceTrackId: string
  sourceTrackType: 'narration' | 'bgm' | 'se'
}
