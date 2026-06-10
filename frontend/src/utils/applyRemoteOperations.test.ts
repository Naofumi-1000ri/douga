/**
 * applyRemoteOperations.test.ts — リモート operation 適用のリグレッションテスト (Issue #285)
 *
 * テスト観点:
 *   (a) 未知の operation type で throw しない(将来 backend が新 type を追加しても
 *       古いフロントのポーリングが止まらない — PR #329 レビュー指摘 CRITICAL)
 *   (b) 未知 type の前後にある既知の operation は正しく適用される
 *   (c) 未知 type 受信時に console.warn が呼ばれる
 *   (d) 既知 operation の基本適用(clip.trim / marker.add / clip.transform flat形式)
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { applyRemoteOperations } from '@/utils/applyRemoteOperations'
import type { Operation } from '@/api/operations'
import type { TimelineData, Clip } from '@/store/projectStore'

function makeClip(id: string, overrides: Partial<Clip> = {}): Clip {
  return {
    id,
    asset_id: 'asset-1',
    start_ms: 0,
    duration_ms: 1000,
    in_point_ms: 0,
    out_point_ms: null,
    transform: { x: 0, y: 0, width: null, height: null, scale: 1, rotation: 0 },
    effects: { opacity: 1 },
    ...overrides,
  }
}

function makeTimeline(): TimelineData {
  return {
    version: '1.0',
    duration_ms: 1000,
    layers: [
      {
        id: 'layer-1',
        name: 'Layer 1',
        order: 0,
        visible: true,
        locked: false,
        clips: [makeClip('clip-1')],
      },
    ],
    audio_tracks: [],
    markers: [],
  }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('applyRemoteOperations', () => {
  it('does not throw on unknown operation types and still applies subsequent operations', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const timeline = makeTimeline()

    // 未知 type(将来 backend が追加する想定)を既知 operation の間に挟む
    const unknownOp = {
      type: 'clip.future_new_op',
      clip_id: 'clip-1',
      data: { some_field: 123 },
    } as unknown as Operation
    const trimOp: Operation = {
      type: 'clip.trim',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { start_ms: 500 },
    }
    const markerOp: Operation = {
      type: 'marker.add',
      marker_id: 'marker-1',
      data: { time_ms: 2000, name: 'M1' },
    }

    let result: TimelineData | undefined
    expect(() => {
      result = applyRemoteOperations(timeline, [trimOp, unknownOp, markerOp])
    }).not.toThrow()

    // 未知 type の前の operation が適用されている
    expect(result!.layers[0].clips[0].start_ms).toBe(500)
    // 未知 type の後の operation も適用されている(throw で中断されない)
    expect(result!.markers).toHaveLength(1)
    expect(result!.markers![0].id).toBe('marker-1')
    expect(result!.markers![0].time_ms).toBe(2000)
    // console.warn で未知 type が報告される
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown operation type'),
      'clip.future_new_op',
    )
  })

  it('does not mutate the input timeline (returns a clone)', () => {
    const timeline = makeTimeline()
    const trimOp: Operation = {
      type: 'clip.trim',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { start_ms: 999 },
    }

    const result = applyRemoteOperations(timeline, [trimOp])

    expect(timeline.layers[0].clips[0].start_ms).toBe(0)
    expect(result.layers[0].clips[0].start_ms).toBe(999)
  })

  it('applies clip.transform with nested payload shape', () => {
    const timeline = makeTimeline()
    const op: Operation = {
      type: 'clip.transform',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { transform: { x: 100, scale: 2 } },
    }

    const result = applyRemoteOperations(timeline, [op])

    const transform = result.layers[0].clips[0].transform
    expect(transform.x).toBe(100)
    expect(transform.scale).toBe(2)
    // 未指定フィールドは保持される
    expect(transform.y).toBe(0)
    expect(transform.rotation).toBe(0)
  })

  it('applies clip.transform with flat payload shape (backend parity)', () => {
    const timeline = makeTimeline()
    // backend の _dispatch_operation と同じく flat 形式も受け付ける
    const op: Operation = {
      type: 'clip.transform',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { x: 50, rotation: 90 },
    }

    const result = applyRemoteOperations(timeline, [op])

    const transform = result.layers[0].clips[0].transform
    expect(transform.x).toBe(50)
    expect(transform.rotation).toBe(90)
    expect(transform.scale).toBe(1)
  })

  it('applies clip.effects with both nested and flat payload shapes', () => {
    const timeline = makeTimeline()
    const nestedOp: Operation = {
      type: 'clip.effects',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { effects: { opacity: 0.5 } },
    }
    const flatOp: Operation = {
      type: 'clip.effects',
      clip_id: 'clip-1',
      layer_id: 'layer-1',
      data: { fade_in_ms: 300 },
    }

    const result = applyRemoteOperations(timeline, [nestedOp, flatOp])

    const effects = result.layers[0].clips[0].effects
    expect(effects.opacity).toBe(0.5)
    expect(effects.fade_in_ms).toBe(300)
  })
})
