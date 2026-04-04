/**
 * timelineGapClose.ts
 *
 * Gap Close のコアロジックを純粋関数として切り出したモジュール。
 * Timeline.tsx の handleCloseGaps から移植。
 * グループクリップ（group_id を持つ映像+音声ペア）は shared delta で連動移動する (#168)。
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

export interface LayerLike {
  id: string
  clips: ClipLike[]
}

export interface TrackLike {
  id: string
  clips: AudioClipLike[]
}

export interface CloseGapsResult<L extends LayerLike, T extends TrackLike> {
  layers: L[]
  audioTracks: T[]
}

/**
 * 同一レイヤー/トラック上の選択クリップ間のギャップを前詰めする。
 * グループクリップは映像クリップの delta を音声クリップにも適用する（shared delta）。
 *
 * ジェネリクスを使って呼び出し元の具体的な型を保持する。
 */
export function closeGaps<L extends LayerLike, T extends TrackLike>(
  layers: L[],
  audioTracks: T[],
  selectedVideoClipIds: Set<string>,
  selectedAudioClipIds: Set<string>,
): CloseGapsResult<L, T> {
  // Phase 1: ビデオクリップの前詰めを計算し、各クリップの delta を記録
  const clipDeltas = new Map<string, number>() // clipId -> deltaMs (負 = 前に移動)
  const updatedLayers = layers.map(layer => {
    const selectedInLayer = layer.clips.filter(c => selectedVideoClipIds.has(c.id))
    if (selectedInLayer.length < 2) return layer

    const sorted = [...selectedInLayer].sort((a, b) => a.start_ms - b.start_ms)
    const clipUpdates = new Map<string, number>() // clipId -> new start_ms

    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]
      const prevNewStart = clipUpdates.get(prev.id) ?? prev.start_ms
      const prevEnd = prevNewStart + prev.duration_ms + (prev.freeze_frame_ms ?? 0)
      const current = sorted[i]
      if (current.start_ms > prevEnd) {
        clipUpdates.set(current.id, prevEnd)
        clipDeltas.set(current.id, prevEnd - current.start_ms) // 負の値 = 前に移動
      }
    }

    if (clipUpdates.size === 0) return layer
    return {
      ...layer,
      clips: layer.clips.map(c => {
        const newStart = clipUpdates.get(c.id)
        return newStart !== undefined ? { ...c, start_ms: newStart } : c
      }),
    }
  }) as L[]

  // Phase 2: 移動した映像クリップの group_id から groupDeltas を構築
  const groupDeltas = new Map<string, number>() // groupId -> deltaMs
  for (const layer of layers) {
    for (const clip of layer.clips) {
      if (clip.group_id && clipDeltas.has(clip.id)) {
        groupDeltas.set(clip.group_id, clipDeltas.get(clip.id)!)
      }
    }
  }

  // Phase 3: オーディオトラックの更新
  // - グループに属する音声クリップ → グループの delta を適用（映像と連動）
  // - グループに属さない選択音声クリップ → 独自に前詰め
  const updatedTracks = audioTracks.map(track => {
    // まずグループ連動を適用
    let updatedClips = track.clips.map(clip => {
      if (clip.group_id && groupDeltas.has(clip.group_id)) {
        const delta = groupDeltas.get(clip.group_id)!
        return { ...clip, start_ms: Math.max(0, clip.start_ms + delta) }
      }
      return clip
    })

    // グループに属さない選択音声クリップの独自前詰め
    const nonGroupSelected = updatedClips.filter(c => selectedAudioClipIds.has(c.id) && !c.group_id)
    if (nonGroupSelected.length >= 2) {
      const sorted = [...nonGroupSelected].sort((a, b) => a.start_ms - b.start_ms)
      const clipUpdates = new Map<string, number>()
      for (let i = 1; i < sorted.length; i++) {
        const prev = sorted[i - 1]
        const prevNewStart = clipUpdates.get(prev.id) ?? prev.start_ms
        const prevEnd = prevNewStart + prev.duration_ms
        const current = sorted[i]
        if (current.start_ms > prevEnd) {
          clipUpdates.set(current.id, prevEnd)
        }
      }
      if (clipUpdates.size > 0) {
        updatedClips = updatedClips.map(c => {
          const newStart = clipUpdates.get(c.id)
          return newStart !== undefined ? { ...c, start_ms: newStart } : c
        })
      }
    }

    return { ...track, clips: updatedClips }
  }) as T[]

  return { layers: updatedLayers, audioTracks: updatedTracks }
}
