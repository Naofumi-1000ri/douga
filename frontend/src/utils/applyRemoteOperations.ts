import type { TimelineData, Layer, Clip, AudioTrack, AudioClip, Marker } from '@/store/projectStore'
import type { Operation } from '@/api/operations'

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
  const type = op.type

  // --- Clip operations ---
  if (type === 'clip.add') {
    const layer = findLayer(tl, op.layer_id)
    if (layer && op.data.clip) {
      layer.clips.push(op.data.clip as unknown as Clip)
    }
  } else if (type === 'clip.delete') {
    const layer = findLayer(tl, op.layer_id)
    if (layer) {
      layer.clips = layer.clips.filter(c => c.id !== op.clip_id)
    }
  } else if (type === 'clip.move') {
    // Move clip: update start_ms, optionally move between layers
    const targetLayerId = op.data.to_layer_id as string | undefined
    const sourceLayer = findLayerWithClip(tl, op.clip_id)
    if (sourceLayer) {
      const clipIdx = sourceLayer.clips.findIndex(c => c.id === op.clip_id)
      if (clipIdx !== -1) {
        const clip = sourceLayer.clips[clipIdx]
        if (op.data.start_ms !== undefined) {
          clip.start_ms = op.data.start_ms as number
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
  } else if (type === 'clip.trim') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      if (op.data.start_ms !== undefined) clip.start_ms = op.data.start_ms as number
      if (op.data.duration_ms !== undefined) clip.duration_ms = op.data.duration_ms as number
      if (op.data.in_point_ms !== undefined) clip.in_point_ms = op.data.in_point_ms as number
      if (op.data.out_point_ms !== undefined) clip.out_point_ms = op.data.out_point_ms as number | null
      if (op.data.speed !== undefined) clip.speed = op.data.speed as number | undefined
    }
  } else if (type === 'clip.transform') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip && op.data.transform) {
      clip.transform = { ...clip.transform, ...(op.data.transform as Record<string, unknown>) } as Clip['transform']
    }
  } else if (type === 'clip.effects') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip && op.data.effects) {
      clip.effects = { ...clip.effects, ...(op.data.effects as Record<string, unknown>) } as Clip['effects']
    }
  } else if (type === 'clip.text') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      clip.text_content = op.data.text_content as string | undefined
    }
  } else if (type === 'clip.text_style') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip && op.data.text_style) {
      clip.text_style = op.data.text_style as Clip['text_style']
    }
  } else if (type === 'clip.shape') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      clip.shape = op.data.shape as Clip['shape']
    }
  } else if (type === 'clip.crop') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      clip.crop = op.data.crop as Clip['crop']
    }
  } else if (type === 'clip.keyframes') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      clip.keyframes = op.data.keyframes as Clip['keyframes']
    }
  } else if (type === 'clip.update') {
    const clip = findClip(tl, op.clip_id, op.layer_id)
    if (clip) {
      Object.assign(clip, op.data)
    }
  }

  // --- Layer operations ---
  else if (type === 'layer.add') {
    const newLayer: Layer = {
      id: op.layer_id!,
      name: (op.data.name as string) || 'New Layer',
      type: op.data.type as Layer['type'],
      order: (op.data.order as number) ?? tl.layers.length,
      visible: (op.data.visible as boolean) ?? true,
      locked: (op.data.locked as boolean) ?? false,
      color: op.data.color as string | undefined,
      clips: (op.data.clips as unknown as Clip[]) || [],
    }
    tl.layers.push(newLayer)
  } else if (type === 'layer.delete') {
    tl.layers = tl.layers.filter(l => l.id !== op.layer_id)
  } else if (type === 'layer.reorder') {
    const order = op.data.order as string[]
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
  } else if (type === 'layer.update') {
    const layer = findLayer(tl, op.layer_id)
    if (layer) {
      if (op.data.name !== undefined) layer.name = op.data.name as string
      if (op.data.type !== undefined) layer.type = op.data.type as Layer['type']
      if (op.data.visible !== undefined) layer.visible = op.data.visible as boolean
      if (op.data.locked !== undefined) layer.locked = op.data.locked as boolean
      if (op.data.color !== undefined) layer.color = op.data.color as string | undefined
      if (op.data.order !== undefined) layer.order = op.data.order as number
    }
  }

  // --- Audio clip operations ---
  else if (type === 'audio_clip.add') {
    const track = findAudioTrack(tl, op.track_id)
    if (track && op.data.clip) {
      track.clips.push(op.data.clip as unknown as AudioClip)
    }
  } else if (type === 'audio_clip.delete') {
    const track = findAudioTrack(tl, op.track_id)
    if (track) {
      track.clips = track.clips.filter(c => c.id !== op.clip_id)
    }
  } else if (type === 'audio_clip.move') {
    const targetTrackId = op.data.to_track_id as string | undefined
    const sourceTrack = findAudioTrackWithClip(tl, op.clip_id)
    if (sourceTrack) {
      const clipIdx = sourceTrack.clips.findIndex(c => c.id === op.clip_id)
      if (clipIdx !== -1) {
        const clip = sourceTrack.clips[clipIdx]
        if (op.data.start_ms !== undefined) {
          clip.start_ms = op.data.start_ms as number
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
  } else if (type === 'audio_clip.update') {
    const clip = findAudioClip(tl, op.clip_id, op.track_id)
    if (clip) {
      Object.assign(clip, op.data)
    }
  }

  // --- Audio track operations ---
  else if (type === 'audio_track.add') {
    const newTrack: AudioTrack = {
      id: op.track_id!,
      name: (op.data.name as string) || 'New Track',
      type: (op.data.type as AudioTrack['type']) || 'bgm',
      volume: (op.data.volume as number) ?? 1,
      muted: (op.data.muted as boolean) ?? false,
      visible: (op.data.visible as boolean) ?? true,
      ducking: op.data.ducking as AudioTrack['ducking'],
      clips: (op.data.clips as unknown as AudioClip[]) || [],
    }
    tl.audio_tracks.push(newTrack)
  } else if (type === 'audio_track.delete') {
    tl.audio_tracks = tl.audio_tracks.filter(t => t.id !== op.track_id)
  } else if (type === 'audio_track.reorder') {
    const order = op.data.order as string[]
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
  } else if (type === 'audio_track.update') {
    const track = findAudioTrack(tl, op.track_id)
    if (track) {
      if (op.data.name !== undefined) track.name = op.data.name as string
      if (op.data.type !== undefined) track.type = op.data.type as AudioTrack['type']
      if (op.data.volume !== undefined) track.volume = op.data.volume as number
      if (op.data.muted !== undefined) track.muted = op.data.muted as boolean
      if (op.data.visible !== undefined) track.visible = op.data.visible as boolean
      if (op.data.ducking !== undefined) track.ducking = op.data.ducking as AudioTrack['ducking']
    }
  }

  // --- Marker operations ---
  else if (type === 'marker.add') {
    if (!tl.markers) tl.markers = []
    const newMarker: Marker = {
      id: op.marker_id!,
      time_ms: op.data.time_ms as number,
      name: (op.data.name as string) || '',
      color: op.data.color as string | undefined,
    }
    tl.markers.push(newMarker)
  } else if (type === 'marker.delete') {
    if (tl.markers) {
      tl.markers = tl.markers.filter(m => m.id !== op.marker_id)
    }
  } else if (type === 'marker.update') {
    if (tl.markers) {
      const marker = tl.markers.find(m => m.id === op.marker_id)
      if (marker) {
        if (op.data.time_ms !== undefined) marker.time_ms = op.data.time_ms as number
        if (op.data.name !== undefined) marker.name = op.data.name as string
        if (op.data.color !== undefined) marker.color = op.data.color as string | undefined
      }
    }
  }

  // --- Timeline full replace (fallback) ---
  else if (type === 'timeline.full_replace') {
    if (op.data.timeline_data) {
      const replacement = op.data.timeline_data as unknown as TimelineData
      tl.version = replacement.version ?? tl.version
      tl.duration_ms = replacement.duration_ms ?? tl.duration_ms
      tl.layers = replacement.layers ?? tl.layers
      tl.audio_tracks = replacement.audio_tracks ?? tl.audio_tracks
      tl.groups = replacement.groups ?? tl.groups
      tl.markers = replacement.markers ?? tl.markers
    }
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
