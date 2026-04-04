import { expect, test } from '@playwright/test'
import { alignLeft, alignRight } from '../src/utils/timelineAlign'

test.describe('Align Clips', () => {
  test('align-right: grouped audio follows video delta, not independent recalculation (regression)', () => {
    // ★ このテストは旧実装（独立再計算）なら FAIL する
    //
    // ビデオ clip A: start=0, dur=1000, freeze=2000 → end=3000
    // ビデオ clip B: start=5000, dur=500 → end=5500
    // 音声 clip A: start=100, dur=900 (group='g1')
    // 音声 clip B: start=5100, dur=400 (group='g2')
    //
    // Right align → maxEnd = 5500
    // clip A: 新start = 5500 - 3000 = 2500, delta = 2500-0 = +2500
    // clip B: 新start = 5500 - 500 = 5000, delta = 5000-5000 = 0
    //
    // 旧実装: a1.start = 5500 - 900 = 4600, a2.start = 5500 - 400 = 5100
    // 新実装: a1.start = 100 + 2500 = 2600 (shared delta from v1)
    //         a2.start = 5100 + 0 = 5100 (shared delta from v2)

    const layers = [{
      id: 'layer1',
      clips: [
        { id: 'v1', start_ms: 0, duration_ms: 1000, freeze_frame_ms: 2000, group_id: 'g1' },
        { id: 'v2', start_ms: 5000, duration_ms: 500, freeze_frame_ms: 0, group_id: 'g2' },
      ]
    }]

    const audioTracks = [{
      id: 'track1',
      clips: [
        { id: 'a1', start_ms: 100, duration_ms: 900, group_id: 'g1' },
        { id: 'a2', start_ms: 5100, duration_ms: 400, group_id: 'g2' },
      ]
    }]

    const selectedVideo = new Set(['v1', 'v2'])
    const selectedAudio = new Set(['a1', 'a2'])

    const result = alignRight(layers, audioTracks, selectedVideo, selectedAudio)

    // Video assertions
    const v1 = result.layers[0].clips.find(c => c.id === 'v1')!
    expect(v1.start_ms).toBe(2500) // 5500 - (1000+2000) = 2500

    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(5000) // 5500 - 500 = 5000 (no change)

    // KEY ASSERTION: audio follows shared delta
    const a1 = result.audioTracks[0].clips.find(c => c.id === 'a1')!
    expect(a1.start_ms).toBe(2600) // 100 + 2500 = 2600, NOT 4600

    const a2 = result.audioTracks[0].clips.find(c => c.id === 'a2')!
    expect(a2.start_ms).toBe(5100) // 5100 + 0 = 5100 (no change)
  })

  test('align-left: grouped audio follows video delta', () => {
    const layers = [{
      id: 'layer1',
      clips: [
        { id: 'v1', start_ms: 3000, duration_ms: 1000, freeze_frame_ms: 500, group_id: 'g1' },
        { id: 'v2', start_ms: 1000, duration_ms: 2000, group_id: 'g2' },
      ]
    }]

    const audioTracks = [{
      id: 'track1',
      clips: [
        { id: 'a1', start_ms: 3100, duration_ms: 900, group_id: 'g1' },
        { id: 'a2', start_ms: 1100, duration_ms: 1800, group_id: 'g2' },
      ]
    }]

    const selectedVideo = new Set(['v1', 'v2'])
    const selectedAudio = new Set(['a1', 'a2'])

    const result = alignLeft(layers, audioTracks, selectedVideo, selectedAudio)

    // minStartMs = 1000 (from v2)
    // v1: delta = 1000 - 3000 = -2000
    // v2: delta = 1000 - 1000 = 0

    const v1 = result.layers[0].clips.find(c => c.id === 'v1')!
    expect(v1.start_ms).toBe(1000)

    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(1000)

    // Audio: shared delta
    const a1 = result.audioTracks[0].clips.find(c => c.id === 'a1')!
    expect(a1.start_ms).toBe(1100) // 3100 + (-2000) = 1100

    const a2 = result.audioTracks[0].clips.find(c => c.id === 'a2')!
    expect(a2.start_ms).toBe(1100) // 1100 + 0 = 1100
  })

  test('non-grouped clips align independently', () => {
    const layers = [{
      id: 'layer1',
      clips: [
        { id: 'v1', start_ms: 5000, duration_ms: 1000 },
        { id: 'v2', start_ms: 2000, duration_ms: 500 },
      ]
    }]
    const audioTracks = [{ id: 'track1', clips: [] }]

    const result = alignLeft(layers, audioTracks, new Set(['v1', 'v2']), new Set())

    const v1 = result.layers[0].clips.find(c => c.id === 'v1')!
    expect(v1.start_ms).toBe(2000)
    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(2000)
  })

  test('align-right: start_ms does not go below zero', () => {
    const layers = [{
      id: 'layer1',
      clips: [
        { id: 'v1', start_ms: 0, duration_ms: 10000, freeze_frame_ms: 5000 },
        { id: 'v2', start_ms: 100, duration_ms: 200 },
      ]
    }]
    const audioTracks = [{ id: 'track1', clips: [] }]

    const result = alignRight(layers, audioTracks, new Set(['v1', 'v2']), new Set())

    // maxEnd = max(15000, 300) = 15000
    // v1: 15000 - 15000 = 0
    // v2: 15000 - 200 = 14800
    const v1 = result.layers[0].clips.find(c => c.id === 'v1')!
    expect(v1.start_ms).toBe(0) // Math.max(0, ...) guard
    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(14800)
  })
})
