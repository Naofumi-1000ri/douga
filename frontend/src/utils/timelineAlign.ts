/**
 * timelineAlign.ts
 *
 * Pure functions for aligning clips in the timeline.
 * Extracted from Timeline.tsx handleAlignLeft / handleAlignRight so that
 * they can be unit-tested without mounting the full React component.
 *
 * Generic types allow the functions to operate on both the minimal test
 * shapes (ClipLike / AudioClipLike) and the full store types (Clip / AudioClip).
 */

export interface ClipLike {
  id: string
  start_ms: number
  duration_ms: number
  freeze_frame_ms?: number | null
  group_id?: string | null
}

export interface AudioClipLike {
  id: string
  start_ms: number
  duration_ms: number
  group_id?: string | null
}

export interface LayerLike<C extends ClipLike = ClipLike> {
  id: string
  clips: C[]
}

export interface TrackLike<A extends AudioClipLike = AudioClipLike> {
  id: string
  clips: A[]
}

export interface AlignResult<
  C extends ClipLike = ClipLike,
  A extends AudioClipLike = AudioClipLike,
> {
  layers: LayerLike<C>[]
  audioTracks: TrackLike<A>[]
}

/**
 * 選択クリップを左揃え（最小 start_ms に合わせる）。
 * グループクリップは映像クリップの delta を音声にも適用。
 */
export function alignLeft<C extends ClipLike, A extends AudioClipLike>(
  layers: LayerLike<C>[],
  audioTracks: TrackLike<A>[],
  selectedVideoClipIds: Set<string>,
  selectedAudioClipIds: Set<string>,
): AlignResult<C, A> {
  let minStartMs = Infinity

  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (selectedVideoClipIds.has(clip.id)) {
        minStartMs = Math.min(minStartMs, clip.start_ms)
      }
    }
  }
  for (const track of audioTracks) {
    for (const clip of track.clips) {
      if (selectedAudioClipIds.has(clip.id)) {
        minStartMs = Math.min(minStartMs, clip.start_ms)
      }
    }
  }

  if (minStartMs === Infinity) {
    return { layers, audioTracks }
  }

  // Phase 1: 各選択映像クリップの delta を計算
  const clipDeltas = new Map<string, number>()
  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (selectedVideoClipIds.has(clip.id)) {
        clipDeltas.set(clip.id, minStartMs - clip.start_ms)
      }
    }
  }

  // Phase 2: グループの delta を収集（映像クリップの delta を使用）
  const groupDeltas = new Map<string, number>()
  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (clip.group_id && clipDeltas.has(clip.id)) {
        groupDeltas.set(clip.group_id, clipDeltas.get(clip.id)!)
      }
    }
  }

  // Phase 3: 映像クリップの更新
  const updatedLayers = layers.map(layer => ({
    ...layer,
    clips: layer.clips.map(clip => {
      if (selectedVideoClipIds.has(clip.id)) {
        return { ...clip, start_ms: minStartMs }
      }
      return clip
    }),
  }))

  // Phase 4: 音声クリップの更新
  // - グループに属する → グループの delta を適用（shared delta）
  // - グループに属さない選択クリップ → 直接 minStartMs に設定
  const updatedTracks = audioTracks.map(track => ({
    ...track,
    clips: track.clips.map(clip => {
      if (clip.group_id && groupDeltas.has(clip.group_id)) {
        // グループ連動: 映像クリップと同じ delta で移動
        const delta = groupDeltas.get(clip.group_id)!
        return { ...clip, start_ms: Math.max(0, clip.start_ms + delta) }
      }
      if (selectedAudioClipIds.has(clip.id)) {
        // グループなし: 直接揃える
        return { ...clip, start_ms: minStartMs }
      }
      return clip
    }),
  }))

  return { layers: updatedLayers, audioTracks: updatedTracks }
}

/**
 * 選択クリップを右揃え（最大 end_ms に合わせる）。
 * グループクリップは映像クリップの delta を音声にも適用。
 */
export function alignRight<C extends ClipLike, A extends AudioClipLike>(
  layers: LayerLike<C>[],
  audioTracks: TrackLike<A>[],
  selectedVideoClipIds: Set<string>,
  selectedAudioClipIds: Set<string>,
): AlignResult<C, A> {
  let maxEndMs = -Infinity

  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (selectedVideoClipIds.has(clip.id)) {
        const end = clip.start_ms + clip.duration_ms + (clip.freeze_frame_ms ?? 0)
        maxEndMs = Math.max(maxEndMs, end)
      }
    }
  }
  for (const track of audioTracks) {
    for (const clip of track.clips) {
      if (selectedAudioClipIds.has(clip.id)) {
        const end = clip.start_ms + clip.duration_ms
        maxEndMs = Math.max(maxEndMs, end)
      }
    }
  }

  if (maxEndMs === -Infinity) {
    return { layers, audioTracks }
  }

  // Phase 1: 各選択映像クリップの delta を計算
  const clipDeltas = new Map<string, number>()
  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (selectedVideoClipIds.has(clip.id)) {
        const clipDuration = clip.duration_ms + (clip.freeze_frame_ms ?? 0)
        const newStart = Math.max(0, maxEndMs - clipDuration)
        clipDeltas.set(clip.id, newStart - clip.start_ms)
      }
    }
  }

  // Phase 2: グループの delta を収集（映像クリップの delta を使用）
  const groupDeltas = new Map<string, number>()
  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (clip.group_id && clipDeltas.has(clip.id)) {
        groupDeltas.set(clip.group_id, clipDeltas.get(clip.id)!)
      }
    }
  }

  // Phase 3: 映像クリップの更新
  const updatedLayers = layers.map(layer => ({
    ...layer,
    clips: layer.clips.map(clip => {
      if (selectedVideoClipIds.has(clip.id)) {
        const clipDuration = clip.duration_ms + (clip.freeze_frame_ms ?? 0)
        return { ...clip, start_ms: Math.max(0, maxEndMs - clipDuration) }
      }
      return clip
    }),
  }))

  // Phase 4: 音声クリップの更新
  // - グループに属する → グループの delta を適用（shared delta）
  // - グループに属さない選択クリップ → 直接右揃え
  const updatedTracks = audioTracks.map(track => ({
    ...track,
    clips: track.clips.map(clip => {
      if (clip.group_id && groupDeltas.has(clip.group_id)) {
        // グループ連動: 映像クリップと同じ delta で移動
        const delta = groupDeltas.get(clip.group_id)!
        return { ...clip, start_ms: Math.max(0, clip.start_ms + delta) }
      }
      if (selectedAudioClipIds.has(clip.id)) {
        // グループなし: 直接揃える
        return { ...clip, start_ms: Math.max(0, maxEndMs - clip.duration_ms) }
      }
      return clip
    }),
  }))

  return { layers: updatedLayers, audioTracks: updatedTracks }
}
