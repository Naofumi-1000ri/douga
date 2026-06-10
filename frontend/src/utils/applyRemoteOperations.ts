import type { TimelineData, Layer, Clip, AudioTrack, AudioClip, Marker } from '@/store/projectStore'
import type { Operation } from '@/api/operations'
import { assertNever } from '@/api/operations'
import { mergeTextStyle, normalizeTextClip } from '@/utils/textStyle'

/**
 * Apply remote operations to a local TimelineData.
 * This is the inverse of diffTimeline — instead of computing diffs,
 * it applies operation patches to produce an updated timeline.
 */
export function applyRemoteOperations(
  timeline: TimelineData,
  operations: Operation[]
): TimelineData {
  const result = structuredClone(timeline)
  for (const op of operations) {
    applyOne(result, op)
  }
  return result
}

function applyOne(tl: TimelineData, op: Operation): void {
  switch (op.type) {
    // --- Clip operations ---
    case 'clip.add': {
      const layer = findLayer(tl, op.layer_id)
      if (layer && op.data.clip) {
        layer.clips.push(normalizeTextClip(op.data.clip))
      }
      break
    }
    case 'clip.delete': {
      const layer = findLayer(tl, op.layer_id)
      if (layer) {
        layer.clips = layer.clips.filter(c => c.id !== op.clip_id)
      }
      break
    }
    case 'clip.move': {
      // Move clip: update start_ms, optionally move between layers
      const targetLayerId = op.data.to_layer_id
      const sourceLayer = findLayerWithClip(tl, op.clip_id)
      if (sourceLayer) {
        const clipIdx = sourceLayer.clips.findIndex(c => c.id === op.clip_id)
        if (clipIdx !== -1) {
          const clip = sourceLayer.clips[clipIdx]
          if (op.data.start_ms !== undefined) {
            clip.start_ms = op.data.start_ms
          }
          // Move to different layer if specified
          if (targetLayerId && targetLayerId !== sourceLayer.id) {
            sourceLayer.clips.splice(clipIdx, 1)
            const targetLayer = findLayer(tl, targetLayerId)
            if (targetLayer) {
              targetLayer.clips.push(clip)
            }
          }
        }
      }
      break
    }
    case 'clip.trim': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        if (op.data.start_ms !== undefined) clip.start_ms = op.data.start_ms
        if (op.data.duration_ms !== undefined) clip.duration_ms = op.data.duration_ms
        if (op.data.in_point_ms !== undefined) clip.in_point_ms = op.data.in_point_ms
        if (op.data.out_point_ms !== undefined) clip.out_point_ms = op.data.out_point_ms
        if (op.data.speed !== undefined) clip.speed = op.data.speed
      }
      break
    }
    case 'clip.transform': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        const transformData = op.data.transform ?? (op.data as Partial<Clip['transform']>)
        clip.transform = { ...clip.transform, ...transformData }
      }
      break
    }
    case 'clip.effects': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        const effectsData = op.data.effects ?? (op.data as Partial<Clip['effects']>)
        clip.effects = { ...clip.effects, ...effectsData }
      }
      break
    }
    case 'clip.text': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        clip.text_content = op.data.text_content
      }
      break
    }
    case 'clip.text_style': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip && op.data.text_style) {
        clip.text_style = mergeTextStyle(
          clip.text_style as Record<string, unknown> | undefined,
          op.data.text_style,
        )
      }
      break
    }
    case 'clip.shape': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        clip.shape = op.data.shape
      }
      break
    }
    case 'clip.crop': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        clip.crop = op.data.crop
      }
      break
    }
    case 'clip.keyframes': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        clip.keyframes = op.data.keyframes
      }
      break
    }
    case 'clip.update': {
      const clip = findClip(tl, op.clip_id, op.layer_id)
      if (clip) {
        Object.assign(clip, op.data)
      }
      break
    }

    // --- Layer operations ---
    case 'layer.add': {
      const newLayer: Layer = {
        id: op.layer_id!,
        name: op.data.name ?? 'New Layer',
        type: op.data.type,
        order: op.data.order ?? tl.layers.length,
        visible: op.data.visible ?? true,
        locked: op.data.locked ?? false,
        color: op.data.color,
        clips: (op.data.clips ?? []).map((clip) => normalizeTextClip(clip)),
      }
      tl.layers.push(newLayer)
      break
    }
    case 'layer.delete': {
      tl.layers = tl.layers.filter(l => l.id !== op.layer_id)
      break
    }
    case 'layer.reorder': {
      const order = op.data.order ?? op.data.layer_ids
      if (order) {
        const layerMap = new Map(tl.layers.map(l => [l.id, l]))
        const reordered: Layer[] = []
        for (const id of order) {
          const layer = layerMap.get(id)
          if (layer) reordered.push(layer)
        }
        // Keep any layers not in the order list at the end
        for (const layer of tl.layers) {
          if (!order.includes(layer.id)) reordered.push(layer)
        }
        tl.layers = reordered
      }
      break
    }
    case 'layer.update': {
      const layer = findLayer(tl, op.layer_id)
      if (layer) {
        if (op.data.name !== undefined) layer.name = op.data.name
        if (op.data.type !== undefined) layer.type = op.data.type
        if (op.data.visible !== undefined) layer.visible = op.data.visible
        if (op.data.locked !== undefined) layer.locked = op.data.locked
        if (op.data.color !== undefined) layer.color = op.data.color
        if (op.data.order !== undefined) layer.order = op.data.order
      }
      break
    }

    // --- Audio clip operations ---
    case 'audio_clip.add': {
      const track = findAudioTrack(tl, op.track_id)
      if (track && op.data.clip) {
        track.clips.push(op.data.clip)
      }
      break
    }
    case 'audio_clip.delete': {
      const track = findAudioTrack(tl, op.track_id)
      if (track) {
        track.clips = track.clips.filter(c => c.id !== op.clip_id)
      }
      break
    }
    case 'audio_clip.move': {
      const targetTrackId = op.data.to_track_id
      const sourceTrack = findAudioTrackWithClip(tl, op.clip_id)
      if (sourceTrack) {
        const clipIdx = sourceTrack.clips.findIndex(c => c.id === op.clip_id)
        if (clipIdx !== -1) {
          const clip = sourceTrack.clips[clipIdx]
          if (op.data.start_ms !== undefined) {
            clip.start_ms = op.data.start_ms
          }
          if (targetTrackId && targetTrackId !== sourceTrack.id) {
            sourceTrack.clips.splice(clipIdx, 1)
            const targetTrack = findAudioTrack(tl, targetTrackId)
            if (targetTrack) {
              targetTrack.clips.push(clip)
            }
          }
        }
      }
      break
    }
    case 'audio_clip.update': {
      const clip = findAudioClip(tl, op.clip_id, op.track_id)
      if (clip) {
        Object.assign(clip, op.data)
      }
      break
    }

    // --- Audio track operations ---
    case 'audio_track.add': {
      const newTrack: AudioTrack = {
        id: op.track_id!,
        name: op.data.name ?? 'New Track',
        type: op.data.type ?? 'bgm',
        volume: op.data.volume ?? 1,
        muted: op.data.muted ?? false,
        visible: op.data.visible ?? true,
        ducking: op.data.ducking,
        clips: op.data.clips ?? [],
      }
      tl.audio_tracks.push(newTrack)
      break
    }
    case 'audio_track.delete': {
      tl.audio_tracks = tl.audio_tracks.filter(t => t.id !== op.track_id)
      break
    }
    case 'audio_track.reorder': {
      const order = op.data.order ?? op.data.track_ids
      if (order) {
        const trackMap = new Map(tl.audio_tracks.map(t => [t.id, t]))
        const reordered: AudioTrack[] = []
        for (const id of order) {
          const track = trackMap.get(id)
          if (track) reordered.push(track)
        }
        for (const track of tl.audio_tracks) {
          if (!order.includes(track.id)) reordered.push(track)
        }
        tl.audio_tracks = reordered
      }
      break
    }
    case 'audio_track.update': {
      const track = findAudioTrack(tl, op.track_id)
      if (track) {
        if (op.data.name !== undefined) track.name = op.data.name
        if (op.data.type !== undefined) track.type = op.data.type
        if (op.data.volume !== undefined) track.volume = op.data.volume
        if (op.data.muted !== undefined) track.muted = op.data.muted
        if (op.data.visible !== undefined) track.visible = op.data.visible
        if (op.data.ducking !== undefined) track.ducking = op.data.ducking
      }
      break
    }

    // --- Marker operations ---
    case 'marker.add': {
      if (!tl.markers) tl.markers = []
      const newMarker: Marker = {
        id: op.marker_id!,
        time_ms: op.data.time_ms ?? 0,
        name: op.data.name ?? '',
        color: op.data.color,
      }
      tl.markers.push(newMarker)
      break
    }
    case 'marker.delete': {
      if (tl.markers) {
        tl.markers = tl.markers.filter(m => m.id !== op.marker_id)
      }
      break
    }
    case 'marker.update': {
      if (tl.markers) {
        const marker = tl.markers.find(m => m.id === op.marker_id)
        if (marker) {
          if (op.data.time_ms !== undefined) marker.time_ms = op.data.time_ms
          if (op.data.name !== undefined) marker.name = op.data.name
          if (op.data.color !== undefined) marker.color = op.data.color
        }
      }
      break
    }

    // --- Timeline full replace (fallback) ---
    case 'timeline.full_replace': {
      if (op.data.timeline_data) {
        const replacement = op.data.timeline_data
        tl.version = replacement.version ?? tl.version
        tl.duration_ms = replacement.duration_ms ?? tl.duration_ms
        tl.layers = replacement.layers ?? tl.layers
        tl.audio_tracks = replacement.audio_tracks ?? tl.audio_tracks
        tl.groups = replacement.groups ?? tl.groups
        tl.markers = replacement.markers ?? tl.markers
      }
      break
    }

    default:
      // Exhaustiveness check: TypeScript will error here if any Operation type is unhandled
      return assertNever(op)
  }
}

// --- Helper functions ---

function findLayer(tl: TimelineData, layerId: string | undefined): Layer | undefined {
  if (!layerId) return undefined
  return tl.layers.find(l => l.id === layerId)
}

function findLayerWithClip(tl: TimelineData, clipId: string | undefined): Layer | undefined {
  if (!clipId) return undefined
  return tl.layers.find(l => l.clips.some(c => c.id === clipId))
}

function findClip(tl: TimelineData, clipId: string | undefined, layerId: string | undefined): Clip | undefined {
  if (!clipId) return undefined
  // Try specified layer first
  if (layerId) {
    const layer = findLayer(tl, layerId)
    if (layer) {
      const clip = layer.clips.find(c => c.id === clipId)
      if (clip) return clip
    }
  }
  // Fallback: search all layers
  for (const layer of tl.layers) {
    const clip = layer.clips.find(c => c.id === clipId)
    if (clip) return clip
  }
  return undefined
}

function findAudioTrack(tl: TimelineData, trackId: string | undefined): AudioTrack | undefined {
  if (!trackId) return undefined
  return tl.audio_tracks.find(t => t.id === trackId)
}

function findAudioTrackWithClip(tl: TimelineData, clipId: string | undefined): AudioTrack | undefined {
  if (!clipId) return undefined
  return tl.audio_tracks.find(t => t.clips.some(c => c.id === clipId))
}

function findAudioClip(tl: TimelineData, clipId: string | undefined, trackId: string | undefined): AudioClip | undefined {
  if (!clipId) return undefined
  // Try specified track first
  if (trackId) {
    const track = findAudioTrack(tl, trackId)
    if (track) {
      const clip = track.clips.find(c => c.id === clipId)
      if (clip) return clip
    }
  }
  // Fallback: search all tracks
  for (const track of tl.audio_tracks) {
    const clip = track.clips.find(c => c.id === clipId)
    if (clip) return clip
  }
  return undefined
}
