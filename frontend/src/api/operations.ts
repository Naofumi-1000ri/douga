import apiClient from './client'
import type { Clip, Layer, AudioClip, AudioTrack, Marker, TimelineData } from '@/store/projectStore'

// ---------------------------------------------------------------------------
// Discriminated union for each operation type
// Each member has a `type` literal that acts as the discriminant.
// The `data` field is fully typed per operation — no more `Record<string, unknown>`.
// ---------------------------------------------------------------------------

export type ClipAddOperation = {
  type: 'clip.add'
  clip_id?: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: { clip: Clip; layer_id?: string }
}

export type ClipMoveOperation = {
  type: 'clip.move'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: { start_ms?: number; to_layer_id?: string; layer_id?: string }
}

export type ClipDeleteOperation = {
  type: 'clip.delete'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: Record<string, never>
}

export type ClipTrimOperation = {
  type: 'clip.trim'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: {
    start_ms?: number
    duration_ms?: number
    in_point_ms?: number
    out_point_ms?: number | null
    speed?: number
  }
}

export type ClipTransformOperation = {
  type: 'clip.transform'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  // The intersection allows BOTH payload shapes the backend accepts:
  // nested `{ transform: { x, y, ... } }` (what timelineDiff emits) and
  // flat `{ x, y, scale, ... }` (other producers, e.g. MCP). The backend's
  // _dispatch_operation unwraps `data.transform` when present, else uses `data` itself.
  data: { transform?: Partial<Clip['transform']> } & Partial<Clip['transform']>
}

export type ClipEffectsOperation = {
  type: 'clip.effects'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  // Same dual-shape contract as ClipTransformOperation: nested `{ effects: {...} }`
  // or flat `{ opacity, chroma_key, ... }` — mirroring the backend's unwrap logic.
  data: { effects?: Partial<Clip['effects']> } & Partial<Clip['effects']>
}

export type ClipTextOperation = {
  type: 'clip.text'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: { text_content?: string }
}

export type ClipTextStyleOperation = {
  type: 'clip.text_style'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  // Nested `{ text_style: {...} }` or flat style fields (backend unwraps either).
  data: { text_style?: Record<string, unknown> } & Record<string, unknown>
}

export type ClipShapeOperation = {
  type: 'clip.shape'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  // Nested `{ shape: {...} }` or flat shape fields (backend unwraps either).
  data: { shape?: Clip['shape'] } & Record<string, unknown>
}

export type ClipCropOperation = {
  type: 'clip.crop'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  // Nested `{ crop: {...} }` or flat crop fields (backend unwraps either).
  data: { crop?: Clip['crop'] } & Record<string, unknown>
}

export type ClipKeyframesOperation = {
  type: 'clip.keyframes'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: { keyframes?: Clip['keyframes'] }
}

export type ClipUpdateOperation = {
  type: 'clip.update'
  clip_id: string
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: Partial<Clip>
}

// --- Layer operations ---

export type LayerAddOperation = {
  type: 'layer.add'
  clip_id?: never
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: Partial<Layer> & { clips?: Clip[]; insert_at?: number }
}

export type LayerDeleteOperation = {
  type: 'layer.delete'
  clip_id?: never
  layer_id: string
  track_id?: never
  marker_id?: never
  data: Record<string, never>
}

export type LayerReorderOperation = {
  type: 'layer.reorder'
  clip_id?: never
  layer_id?: string
  track_id?: never
  marker_id?: never
  data: { order?: string[]; layer_ids?: string[] }
}

export type LayerUpdateOperation = {
  type: 'layer.update'
  clip_id?: never
  layer_id: string
  track_id?: never
  marker_id?: never
  data: Partial<Pick<Layer, 'name' | 'type' | 'visible' | 'locked' | 'color' | 'order'>>
}

// --- Audio clip operations ---

export type AudioClipAddOperation = {
  type: 'audio_clip.add'
  clip_id?: string
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: { clip: AudioClip; track_id?: string }
}

export type AudioClipMoveOperation = {
  type: 'audio_clip.move'
  clip_id: string
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: { start_ms?: number; to_track_id?: string; track_id?: string }
}

export type AudioClipDeleteOperation = {
  type: 'audio_clip.delete'
  clip_id: string
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: Record<string, never>
}

export type AudioClipUpdateOperation = {
  type: 'audio_clip.update'
  clip_id: string
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: Partial<AudioClip>
}

// --- Audio track operations ---

export type AudioTrackAddOperation = {
  type: 'audio_track.add'
  clip_id?: never
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: Partial<AudioTrack> & { clips?: AudioClip[] }
}

export type AudioTrackDeleteOperation = {
  type: 'audio_track.delete'
  clip_id?: never
  layer_id?: never
  track_id: string
  marker_id?: never
  data: Record<string, never>
}

export type AudioTrackReorderOperation = {
  type: 'audio_track.reorder'
  clip_id?: never
  layer_id?: never
  track_id?: string
  marker_id?: never
  data: { order?: string[]; track_ids?: string[] }
}

export type AudioTrackUpdateOperation = {
  type: 'audio_track.update'
  clip_id?: never
  layer_id?: never
  track_id: string
  marker_id?: never
  data: Partial<Pick<AudioTrack, 'name' | 'type' | 'volume' | 'muted' | 'visible' | 'ducking'>>
}

// --- Marker operations ---

export type MarkerAddOperation = {
  type: 'marker.add'
  clip_id?: never
  layer_id?: never
  track_id?: never
  marker_id?: string
  data: Partial<Marker>
}

export type MarkerUpdateOperation = {
  type: 'marker.update'
  clip_id?: never
  layer_id?: never
  track_id?: never
  marker_id: string
  data: Partial<Marker>
}

export type MarkerDeleteOperation = {
  type: 'marker.delete'
  clip_id?: never
  layer_id?: never
  track_id?: never
  marker_id: string
  data: Record<string, never>
}

// --- Timeline operations ---

export type TimelineFullReplaceOperation = {
  type: 'timeline.full_replace'
  clip_id?: never
  layer_id?: never
  track_id?: never
  marker_id?: never
  data: { timeline_data?: Partial<TimelineData> }
}

// ---------------------------------------------------------------------------
// Main discriminated union
// ---------------------------------------------------------------------------

export type Operation =
  | ClipAddOperation
  | ClipMoveOperation
  | ClipDeleteOperation
  | ClipTrimOperation
  | ClipTransformOperation
  | ClipEffectsOperation
  | ClipTextOperation
  | ClipTextStyleOperation
  | ClipShapeOperation
  | ClipCropOperation
  | ClipKeyframesOperation
  | ClipUpdateOperation
  | LayerAddOperation
  | LayerDeleteOperation
  | LayerReorderOperation
  | LayerUpdateOperation
  | AudioClipAddOperation
  | AudioClipMoveOperation
  | AudioClipDeleteOperation
  | AudioClipUpdateOperation
  | AudioTrackAddOperation
  | AudioTrackDeleteOperation
  | AudioTrackReorderOperation
  | AudioTrackUpdateOperation
  | MarkerAddOperation
  | MarkerUpdateOperation
  | MarkerDeleteOperation
  | TimelineFullReplaceOperation

// NOTE: Exhaustiveness checking is done at the consumer side (applyRemoteOperations)
// via a `const unhandled: never = op` assignment in the switch default branch.
// A throwing assertNever() was deliberately removed: throwing on unknown types at
// runtime would stall the operation polling loop when an older frontend receives
// operation types introduced by a newer backend.

// ---------------------------------------------------------------------------
// Retained shared interfaces
// ---------------------------------------------------------------------------

export interface ApplyOperationsResponse {
  version: number
  timeline_data: Record<string, unknown>
}

export interface OperationHistoryItem {
  id: string
  version: number
  type: string
  user_id: string | null
  user_name: string | null
  data: Record<string, unknown>
  created_at: string
}

export interface OperationHistoryResponse {
  current_version: number
  operations: OperationHistoryItem[]
}

export const operationsApi = {
  apply: async (projectId: string, version: number, operations: Operation[]): Promise<ApplyOperationsResponse> => {
    const res = await apiClient.post(`/projects/${projectId}/operations`, {
      version,
      operations,
    })
    return res.data
  },

  poll: async (projectId: string, sinceVersion: number, limit: number = 50): Promise<OperationHistoryResponse> => {
    const res = await apiClient.get(`/projects/${projectId}/operations`, {
      params: { since_version: sinceVersion, limit },
    })
    return res.data
  },
}
