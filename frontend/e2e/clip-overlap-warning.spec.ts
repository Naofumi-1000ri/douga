/**
 * E2E / unit tests for Issue #177: clip overlap detection
 *
 * Tests the detectOverlaps utility which powers the overlap warning
 * indicators on Timeline clips.
 */
import { expect, test } from '@playwright/test'
import { detectOverlaps } from '../src/utils/clipOverlap'

test.describe('Clip overlap detection (#177)', () => {
  test('no overlap returns empty map', () => {
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 1000 },
      { id: 'b', start_ms: 1000, duration_ms: 1000 },
      { id: 'c', start_ms: 2000, duration_ms: 1000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  test('adjacent clips (touching but not overlapping) are not flagged', () => {
    // Clips that share exactly the same boundary point should NOT overlap:
    // a ends at 1000, b starts at 1000 → no overlap
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 1000 },
      { id: 'b', start_ms: 1000, duration_ms: 500 },
    ]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  test('two overlapping clips are both flagged', () => {
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 2000 },
      { id: 'b', start_ms: 1000, duration_ms: 2000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.has('a')).toBe(true)
    expect(result.has('b')).toBe(true)
    expect(result.get('a')!.has('b')).toBe(true)
    expect(result.get('b')!.has('a')).toBe(true)
  })

  test('one clip fully containing another - both flagged', () => {
    const clips = [
      { id: 'outer', start_ms: 0, duration_ms: 5000 },
      { id: 'inner', start_ms: 1000, duration_ms: 1000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.has('outer')).toBe(true)
    expect(result.has('inner')).toBe(true)
  })

  test('three-way overlap: all three clips overlap each other', () => {
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 3000 },
      { id: 'b', start_ms: 1000, duration_ms: 3000 },
      { id: 'c', start_ms: 2000, duration_ms: 3000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.has('a')).toBe(true)
    expect(result.has('b')).toBe(true)
    expect(result.has('c')).toBe(true)
    // a overlaps b and c
    expect(result.get('a')!.has('b')).toBe(true)
    expect(result.get('a')!.has('c')).toBe(true)
  })

  test('clip count in overlap set is correct', () => {
    // a overlaps both b and c → a's set should have 2 entries
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 5000 },
      { id: 'b', start_ms: 1000, duration_ms: 1000 },
      { id: 'c', start_ms: 3000, duration_ms: 1000 },
      { id: 'd', start_ms: 6000, duration_ms: 1000 },
    ]
    const result = detectOverlaps(clips)
    // a overlaps b, c (both within 0..5000)
    expect(result.get('a')!.size).toBe(2)
    // d is outside a's range, no overlap
    expect(result.has('d')).toBe(false)
  })

  test('single clip returns empty map', () => {
    const clips = [{ id: 'solo', start_ms: 0, duration_ms: 1000 }]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  test('empty clip list returns empty map', () => {
    const result = detectOverlaps([])
    expect(result.size).toBe(0)
  })
})
