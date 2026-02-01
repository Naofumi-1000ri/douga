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
}

export interface DragState {
  type: 'move' | 'trim-start' | 'trim-end'
  trackId: string
  clipId: string
  startX: number
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  assetDurationMs: number
  currentDeltaMs: number
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
}

export interface VideoDragState {
  type: 'move' | 'trim-start' | 'trim-end'
  layerId: string
  clipId: string
  startX: number
  initialStartMs: number
  initialDurationMs: number
  initialInPointMs: number
  initialOutPointMs: number
  initialSpeed: number
  assetDurationMs: number
  currentDeltaMs: number
  isResizableClip: boolean
  isVideoAsset: boolean
  groupId?: string | null
  groupVideoClips?: GroupClipInitialPosition[]
  groupAudioClips?: GroupClipInitialPosition[]
}
