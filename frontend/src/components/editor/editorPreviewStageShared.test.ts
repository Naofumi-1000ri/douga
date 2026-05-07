import { describe, it, expect, vi } from 'vitest'

// buildActivePreviewClips が依存する外部モジュールをモック
vi.mock('@/utils/textStyle', () => ({
  normalizeTextClip: (clip: unknown) => clip,
}))

vi.mock('@/utils/keyframes', () => ({
  getInterpolatedTransform: () => null,
}))

import { buildActivePreviewClips } from './editorPreviewStageShared'
import type { TimelineData } from '@/store/projectStore'

function makeClip(id: string, startMs: number, durationMs: number, freezeFrameMs = 0) {
  return {
    id,
    start_ms: startMs,
    duration_ms: durationMs,
    freeze_frame_ms: freezeFrameMs,
    asset_id: null,
    shape: null,
    text_content: undefined,
    transform: { x: 0, y: 0, scale: 1, rotation: 0 },
    effects: { opacity: 1 },
    keyframes: [],
    in_point_ms: 0,
    speed: 1,
  }
}

function makeTimelineData(clip: ReturnType<typeof makeClip>): TimelineData {
  return {
    version: '1',
    duration_ms: 10000,
    layers: [
      {
        id: 'layer-1',
        name: 'Layer 1',
        order: 0,
        visible: true,
        locked: false,
        clips: [clip as never],
      },
    ],
    audio_tracks: [],
  }
}

describe('buildActivePreviewClips', () => {
  const baseArgs = {
    assets: [],
    dragTransform: null,
    previewDrag: null,
  }

  describe('選択中クリップの末尾判定', () => {
    it('選択中クリップは currentTime === endMs のとき結果に含まれる（修正後の正常動作）', () => {
      const clip = makeClip('clip-A', 0, 1000)
      const timelineData = makeTimelineData(clip)

      const result = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 1000, // == start_ms + duration_ms
        timelineData,
        selectedClipId: 'clip-A',
      })

      const ids = result.map((c) => c.clip.id)
      expect(ids).toContain('clip-A')
    })

    it('選択していないクリップは currentTime === endMs のとき結果に含まれない（二重描画防止）', () => {
      const clip = makeClip('clip-A', 0, 1000)
      const timelineData = makeTimelineData(clip)

      const result = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 1000,
        timelineData,
        selectedClipId: null,
      })

      const ids = result.map((c) => c.clip.id)
      expect(ids).not.toContain('clip-A')
    })

    it('selectedClipId が別のクリップを指す場合、clip-A は currentTime === endMs で含まれない', () => {
      const clip = makeClip('clip-A', 0, 1000)
      const timelineData = makeTimelineData(clip)

      const result = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 1000,
        timelineData,
        selectedClipId: 'clip-B', // clip-A ではない
      })

      const ids = result.map((c) => c.clip.id)
      expect(ids).not.toContain('clip-A')
    })

    it('currentTime がクリップ範囲内のとき、選択有無に関わらず含まれる', () => {
      const clip = makeClip('clip-A', 0, 1000)
      const timelineData = makeTimelineData(clip)

      const resultSelected = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 500,
        timelineData,
        selectedClipId: 'clip-A',
      })
      const resultUnselected = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 500,
        timelineData,
        selectedClipId: null,
      })

      expect(resultSelected.map((c) => c.clip.id)).toContain('clip-A')
      expect(resultUnselected.map((c) => c.clip.id)).toContain('clip-A')
    })

    it('freeze_frame_ms がある場合の末尾判定（選択中クリップ）', () => {
      // start=0, duration=1000, freeze=200 => endMs=1200
      const clip = makeClip('clip-A', 0, 1000, 200)
      const timelineData = makeTimelineData(clip)

      const result = buildActivePreviewClips({
        ...baseArgs,
        currentTime: 1200, // == endMs
        timelineData,
        selectedClipId: 'clip-A',
      })

      expect(result.map((c) => c.clip.id)).toContain('clip-A')
    })
  })
})
