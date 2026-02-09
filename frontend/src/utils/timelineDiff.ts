import type { TimelineData, Layer, AudioTrack, Clip, AudioClip, Marker } from '@/store/projectStore'
import type { Operation } from '@/api/operations'

const MAX_OPERATIONS = 50

function jsonEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function shallowDiff(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  keys: string[]
): Record<string, unknown> | null {
  const changes: Record<string, unknown> = {}
  let hasChanges = false
  for (const key of keys) {
    if (!jsonEqual(a[key], b[key])) {
      changes[key] = b[key]
      hasChanges = true
    }
  }
  return hasChanges ? changes : null
}

function diffClips(oldClips: Clip[], newClips: Clip[], layerId: string, ops: Operation[]): void {
  const oldMap = new Map(oldClips.map(c => [c.id, c]))
  const newMap = new Map(newClips.map(c => [c.id, c]))

  // Deleted clips
  for (const old of oldClips) {
    if (!newMap.has(old.id)) {
      ops.push({ type: 'clip.delete', clip_id: old.id, layer_id: layerId, data: {} })
    }
  }

  // Added clips
  for (const nc of newClips) {
    if (!oldMap.has(nc.id)) {
      ops.push({
        type: 'clip.add',
        clip_id: nc.id,
        layer_id: layerId,
        data: { clip: nc as unknown as Record<string, unknown> },
      })
    }
  }

  // Modified clips
  for (const nc of newClips) {
    const oc = oldMap.get(nc.id)
    if (!oc) continue
    if (jsonEqual(oc, nc)) continue

    // Determine change type based on what fields changed
    if (oc.start_ms !== nc.start_ms || oc.duration_ms !== nc.duration_ms ||
        oc.in_point_ms !== nc.in_point_ms || !jsonEqual(oc.out_point_ms, nc.out_point_ms) ||
        oc.speed !== nc.speed) {
      ops.push({
        type: 'clip.trim',
        clip_id: nc.id,
        layer_id: layerId,
        data: {
          start_ms: nc.start_ms,
          duration_ms: nc.duration_ms,
          in_point_ms: nc.in_point_ms,
          out_point_ms: nc.out_point_ms,
          speed: nc.speed,
        },
      })
    }

    if (!jsonEqual(oc.transform, nc.transform)) {
      ops.push({
        type: 'clip.transform',
        clip_id: nc.id,
        layer_id: layerId,
        data: { transform: nc.transform as unknown as Record<string, unknown> },
      })
    }

    if (!jsonEqual(oc.effects, nc.effects)) {
      ops.push({
        type: 'clip.effects',
        clip_id: nc.id,
        layer_id: layerId,
        data: { effects: nc.effects as unknown as Record<string, unknown> },
      })
    }

    if (oc.text_content !== nc.text_content) {
      ops.push({
        type: 'clip.text',
        clip_id: nc.id,
        layer_id: layerId,
        data: { text_content: nc.text_content },
      })
    }

    if (!jsonEqual(oc.text_style, nc.text_style)) {
      ops.push({
        type: 'clip.text_style',
        clip_id: nc.id,
        layer_id: layerId,
        data: { text_style: nc.text_style as unknown as Record<string, unknown> },
      })
    }

    if (!jsonEqual(oc.shape, nc.shape)) {
      ops.push({
        type: 'clip.shape',
        clip_id: nc.id,
        layer_id: layerId,
        data: { shape: nc.shape as unknown as Record<string, unknown> },
      })
    }

    if (!jsonEqual(oc.crop, nc.crop)) {
      ops.push({
        type: 'clip.crop',
        clip_id: nc.id,
        layer_id: layerId,
        data: { crop: nc.crop as unknown as Record<string, unknown> },
      })
    }

    if (!jsonEqual(oc.keyframes, nc.keyframes)) {
      ops.push({
        type: 'clip.keyframes',
        clip_id: nc.id,
        layer_id: layerId,
        data: { keyframes: nc.keyframes as unknown as Record<string, unknown> },
      })
    }

    // Check other fields (group_id, fade_in_ms, fade_out_ms, asset_id)
    const otherDiff = shallowDiff(
      oc as unknown as Record<string, unknown>,
      nc as unknown as Record<string, unknown>,
      ['group_id', 'fade_in_ms', 'fade_out_ms', 'asset_id']
    )
    if (otherDiff) {
      ops.push({
        type: 'clip.update',
        clip_id: nc.id,
        layer_id: layerId,
        data: otherDiff,
      })
    }
  }
}

function diffLayers(oldLayers: Layer[], newLayers: Layer[], ops: Operation[]): void {
  const oldMap = new Map(oldLayers.map(l => [l.id, l]))
  const newMap = new Map(newLayers.map(l => [l.id, l]))

  // Deleted layers
  for (const old of oldLayers) {
    if (!newMap.has(old.id)) {
      ops.push({ type: 'layer.delete', layer_id: old.id, data: {} })
    }
  }

  // Added layers
  for (const nl of newLayers) {
    if (!oldMap.has(nl.id)) {
      ops.push({
        type: 'layer.add',
        layer_id: nl.id,
        data: {
          name: nl.name,
          type: nl.type,
          order: nl.order,
          visible: nl.visible,
          locked: nl.locked,
          color: nl.color,
          clips: nl.clips as unknown as Record<string, unknown>,
        },
      })
    }
  }

  // Modified layers
  for (const nl of newLayers) {
    const ol = oldMap.get(nl.id)
    if (!ol) continue

    // Check layer properties
    const propDiff = shallowDiff(
      { name: ol.name, type: ol.type, visible: ol.visible, locked: ol.locked, color: ol.color, order: ol.order },
      { name: nl.name, type: nl.type, visible: nl.visible, locked: nl.locked, color: nl.color, order: nl.order },
      ['name', 'type', 'visible', 'locked', 'color', 'order']
    )
    if (propDiff) {
      ops.push({ type: 'layer.update', layer_id: nl.id, data: propDiff })
    }

    // Compare clips within this layer
    diffClips(ol.clips, nl.clips, nl.id, ops)
  }

  // Check layer order change
  const oldOrder = oldLayers.map(l => l.id)
  const newOrder = newLayers.map(l => l.id)
  if (!jsonEqual(oldOrder, newOrder) && oldOrder.length === newOrder.length) {
    // Only emit reorder if no adds/deletes (those are handled separately)
    const oldSet = new Set(oldOrder)
    const newSet = new Set(newOrder)
    const sameSet = oldOrder.length === newOrder.length &&
      oldOrder.every(id => newSet.has(id)) &&
      newOrder.every(id => oldSet.has(id))
    if (sameSet) {
      ops.push({ type: 'layer.reorder', data: { order: newOrder } })
    }
  }
}

function diffAudioClips(oldClips: AudioClip[], newClips: AudioClip[], trackId: string, ops: Operation[]): void {
  const oldMap = new Map(oldClips.map(c => [c.id, c]))
  const newMap = new Map(newClips.map(c => [c.id, c]))

  // Deleted
  for (const oc of oldClips) {
    if (!newMap.has(oc.id)) {
      ops.push({ type: 'audio_clip.delete', clip_id: oc.id, track_id: trackId, data: {} })
    }
  }

  // Added
  for (const nc of newClips) {
    if (!oldMap.has(nc.id)) {
      ops.push({
        type: 'audio_clip.add',
        clip_id: nc.id,
        track_id: trackId,
        data: { clip: nc as unknown as Record<string, unknown> },
      })
    }
  }

  // Modified
  for (const nc of newClips) {
    const oc = oldMap.get(nc.id)
    if (!oc) continue
    if (jsonEqual(oc, nc)) continue

    const diff = shallowDiff(
      oc as unknown as Record<string, unknown>,
      nc as unknown as Record<string, unknown>,
      ['start_ms', 'duration_ms', 'in_point_ms', 'out_point_ms', 'volume', 'fade_in_ms', 'fade_out_ms', 'group_id', 'volume_keyframes']
    )
    if (diff) {
      ops.push({ type: 'audio_clip.update', clip_id: nc.id, track_id: trackId, data: diff })
    }
  }
}

function diffAudioTracks(oldTracks: AudioTrack[], newTracks: AudioTrack[], ops: Operation[]): void {
  const oldMap = new Map(oldTracks.map(t => [t.id, t]))
  const newMap = new Map(newTracks.map(t => [t.id, t]))

  // Deleted
  for (const ot of oldTracks) {
    if (!newMap.has(ot.id)) {
      ops.push({ type: 'audio_track.delete', track_id: ot.id, data: {} })
    }
  }

  // Added
  for (const nt of newTracks) {
    if (!oldMap.has(nt.id)) {
      ops.push({
        type: 'audio_track.add',
        track_id: nt.id,
        data: {
          name: nt.name,
          type: nt.type,
          volume: nt.volume,
          muted: nt.muted,
          visible: nt.visible,
          ducking: nt.ducking as unknown as Record<string, unknown>,
          clips: nt.clips as unknown as Record<string, unknown>,
        },
      })
    }
  }

  // Modified
  for (const nt of newTracks) {
    const ot = oldMap.get(nt.id)
    if (!ot) continue

    // Check track properties
    const propDiff = shallowDiff(
      { name: ot.name, type: ot.type, volume: ot.volume, muted: ot.muted, visible: ot.visible, ducking: ot.ducking },
      { name: nt.name, type: nt.type, volume: nt.volume, muted: nt.muted, visible: nt.visible, ducking: nt.ducking },
      ['name', 'type', 'volume', 'muted', 'visible', 'ducking']
    )
    if (propDiff) {
      ops.push({ type: 'audio_track.update', track_id: nt.id, data: propDiff })
    }

    // Compare audio clips
    diffAudioClips(ot.clips, nt.clips, nt.id, ops)
  }

  // Check track order
  const oldOrder = oldTracks.map(t => t.id)
  const newOrder = newTracks.map(t => t.id)
  if (!jsonEqual(oldOrder, newOrder) && oldOrder.length === newOrder.length) {
    const oldSet = new Set(oldOrder)
    const newSet = new Set(newOrder)
    const sameSet = oldOrder.every(id => newSet.has(id)) && newOrder.every(id => oldSet.has(id))
    if (sameSet) {
      ops.push({ type: 'audio_track.reorder', data: { order: newOrder } })
    }
  }
}

function diffMarkers(oldMarkers: Marker[], newMarkers: Marker[], ops: Operation[]): void {
  const oldMap = new Map(oldMarkers.map(m => [m.id, m]))
  const newMap = new Map(newMarkers.map(m => [m.id, m]))

  // Deleted
  for (const om of oldMarkers) {
    if (!newMap.has(om.id)) {
      ops.push({ type: 'marker.delete', marker_id: om.id, data: {} })
    }
  }

  // Added
  for (const nm of newMarkers) {
    if (!oldMap.has(nm.id)) {
      ops.push({
        type: 'marker.add',
        marker_id: nm.id,
        data: { time_ms: nm.time_ms, name: nm.name, color: nm.color },
      })
    }
  }

  // Modified
  for (const nm of newMarkers) {
    const om = oldMap.get(nm.id)
    if (!om) continue
    if (jsonEqual(om, nm)) continue
    const diff = shallowDiff(
      om as unknown as Record<string, unknown>,
      nm as unknown as Record<string, unknown>,
      ['time_ms', 'name', 'color']
    )
    if (diff) {
      ops.push({ type: 'marker.update', marker_id: nm.id, data: diff })
    }
  }
}

export function diffTimeline(oldTl: TimelineData, newTl: TimelineData): Operation[] {
  const ops: Operation[] = []

  // Compare layers (including clips within each layer)
  diffLayers(oldTl.layers, newTl.layers, ops)

  // Compare audio tracks (including audio clips)
  diffAudioTracks(oldTl.audio_tracks || [], newTl.audio_tracks || [], ops)

  // Compare markers
  diffMarkers(oldTl.markers || [], newTl.markers || [], ops)

  // Fallback: if too many operations, use full_replace
  if (ops.length > MAX_OPERATIONS) {
    return [{ type: 'timeline.full_replace', data: { timeline_data: newTl as unknown as Record<string, unknown> } }]
  }

  // If no ops detected but timelines differ, use full_replace
  if (ops.length === 0 && !jsonEqual(oldTl, newTl)) {
    return [{ type: 'timeline.full_replace', data: { timeline_data: newTl as unknown as Record<string, unknown> } }]
  }

  return ops
}
