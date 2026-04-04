import { expect, test } from '@playwright/test'
import { closeGaps } from '../src/utils/timelineGapClose'

test.describe('Gap Close', () => {
  test('grouped video+audio clips move by shared delta when closing gaps', () => {
    // ビデオ clip A: start=0, dur=1000, freeze=500 → end=1500
    // ビデオ clip B: start=2000, dur=1000 → gap=500ms, delta=-500
    // 音声 clip A: start=0, dur=1500 (group_id='g1')
    // 音声 clip B: start=2000, dur=1000 (group_id='g2' — clip B と同じグループ)
    const layers = [
      {
        id: 'layer1',
        clips: [
          { id: 'v1', start_ms: 0, duration_ms: 1000, freeze_frame_ms: 500, group_id: 'g1' },
          { id: 'v2', start_ms: 2000, duration_ms: 1000, freeze_frame_ms: 0, group_id: 'g2' },
        ],
      },
    ]

    const audioTracks = [
      {
        id: 'track1',
        clips: [
          { id: 'a1', start_ms: 0, duration_ms: 1500, group_id: 'g1' },
          { id: 'a2', start_ms: 2000, duration_ms: 1000, group_id: 'g2' },
        ],
      },
    ]

    const selectedVideo = new Set(['v1', 'v2'])
    const selectedAudio = new Set(['a1', 'a2'])

    const result = closeGaps(layers, audioTracks, selectedVideo, selectedAudio)

    // ビデオ clip B は clip A の末尾（0+1000+500=1500）に詰まる
    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(1500) // 2000 - 500 = 1500

    // 音声 clip B はグループ g2 の delta (-500) で連動
    const a2 = result.audioTracks[0].clips.find(c => c.id === 'a2')!
    expect(a2.start_ms).toBe(1500) // 2000 - 500 = 1500
  })

  test('audio clip follows video delta, not its own gap calculation (regression)', () => {
    // ★ このテストは旧実装（video/audio独立前詰め）なら FAIL する
    //
    // ビデオ clip A: start=0, dur=1000, freeze=2000 → end=3000
    // ビデオ clip B: start=4000, dur=1000 → gap=1000ms, delta=-1000
    // 音声 clip A: start=0, dur=1000 (group='g1') → end=1000
    // 音声 clip B: start=4000, dur=1000 (group='g2')
    //
    // 旧実装: 音声独自に前詰め → a2.start = a1.end = 1000 (delta=-3000)
    // 新実装: 音声はビデオの delta で連動 → a2.start = 4000 - 1000 = 3000 (delta=-1000)
    const layers = [
      {
        id: 'layer1',
        clips: [
          { id: 'v1', start_ms: 0, duration_ms: 1000, freeze_frame_ms: 2000, group_id: 'g1' },
          { id: 'v2', start_ms: 4000, duration_ms: 1000, freeze_frame_ms: 0, group_id: 'g2' },
        ],
      },
    ]

    const audioTracks = [
      {
        id: 'track1',
        clips: [
          { id: 'a1', start_ms: 0, duration_ms: 1000, group_id: 'g1' },
          { id: 'a2', start_ms: 4000, duration_ms: 1000, group_id: 'g2' },
        ],
      },
    ]

    const selectedVideo = new Set(['v1', 'v2'])
    const selectedAudio = new Set(['a1', 'a2'])

    const result = closeGaps(layers, audioTracks, selectedVideo, selectedAudio)

    const v2 = result.layers[0].clips.find(c => c.id === 'v2')!
    expect(v2.start_ms).toBe(3000) // 4000 - 1000 = 3000 (gap closed)

    const a2 = result.audioTracks[0].clips.find(c => c.id === 'a2')!
    // KEY ASSERTION: 音声は映像の delta (-1000) で移動、独自前詰め (-3000) ではない
    expect(a2.start_ms).toBe(3000) // NOT 1000
  })

  test('non-grouped audio clips still close gaps independently', () => {
    const layers = [{ id: 'layer1', clips: [] }]
    const audioTracks = [
      {
        id: 'track1',
        clips: [
          { id: 'a1', start_ms: 0, duration_ms: 1000 }, // no group
          { id: 'a2', start_ms: 2000, duration_ms: 500 }, // gap=1000
        ],
      },
    ]

    const result = closeGaps(layers, audioTracks, new Set(), new Set(['a1', 'a2']))

    const a2 = result.audioTracks[0].clips.find(c => c.id === 'a2')!
    expect(a2.start_ms).toBe(1000) // independent gap close
  })
})
