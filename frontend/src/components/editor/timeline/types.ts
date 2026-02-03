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
}

export interface VideoDragState {
  type: 'move' | 'trim-start' | 'trim-end' | 'stretch-start' | 'stretch-end'
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
}

// Cross-layer drop preview state
export interface CrossLayerDropPreview {
  layerId: string
  timeMs: number
  durationMs: number
}
