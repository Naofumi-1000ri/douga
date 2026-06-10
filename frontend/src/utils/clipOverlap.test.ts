/**
 * Unit tests for Issue #177: clip overlap detection
 *
 * detectOverlaps / detectOverlapsInGroups power the overlap warning
 * indicators (orange border + warning icon) on Timeline clips.
 */
import { describe, expect, it } from 'vitest'
import { detectOverlaps, detectOverlapsInGroups } from './clipOverlap'

describe('detectOverlaps (#177)', () => {
  it('no overlap returns empty map', () => {
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 1000 },
      { id: 'b', start_ms: 1000, duration_ms: 1000 },
      { id: 'c', start_ms: 2000, duration_ms: 1000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  it('adjacent clips (touching but not overlapping) are not flagged', () => {
    // Clips that share exactly the same boundary point should NOT overlap:
    // a ends at 1000, b starts at 1000 → no overlap
    const clips = [
      { id: 'a', start_ms: 0, duration_ms: 1000 },
      { id: 'b', start_ms: 1000, duration_ms: 500 },
    ]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  it('two overlapping clips are both flagged', () => {
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

  it('one clip fully containing another - both flagged', () => {
    const clips = [
      { id: 'outer', start_ms: 0, duration_ms: 5000 },
      { id: 'inner', start_ms: 1000, duration_ms: 1000 },
    ]
    const result = detectOverlaps(clips)
    expect(result.has('outer')).toBe(true)
    expect(result.has('inner')).toBe(true)
  })

  it('three-way overlap: all three clips overlap each other', () => {
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

  it('clip count in overlap set is correct', () => {
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

  it('unsorted input is handled (sorting is internal)', () => {
    const clips = [
      { id: 'late', start_ms: 5000, duration_ms: 2000 },
      { id: 'early', start_ms: 0, duration_ms: 2000 },
      { id: 'mid', start_ms: 1000, duration_ms: 5000 }, // overlaps both
    ]
    const result = detectOverlaps(clips)
    expect(result.get('mid')!.size).toBe(2)
    expect(result.get('early')!.has('mid')).toBe(true)
    expect(result.get('late')!.has('mid')).toBe(true)
    expect(result.get('early')!.has('late')).toBe(false)
  })

  it('single clip returns empty map', () => {
    const clips = [{ id: 'solo', start_ms: 0, duration_ms: 1000 }]
    const result = detectOverlaps(clips)
    expect(result.size).toBe(0)
  })

  it('empty clip list returns empty map', () => {
    const result = detectOverlaps([])
    expect(result.size).toBe(0)
  })
})

describe('detectOverlapsInGroups (#177)', () => {
  it('detects overlaps within each group independently', () => {
    const groups = [
      {
        clips: [
          { id: 'l1-a', start_ms: 0, duration_ms: 2000 },
          { id: 'l1-b', start_ms: 1000, duration_ms: 2000 }, // overlaps l1-a
        ],
      },
      {
        clips: [
          { id: 'l2-a', start_ms: 0, duration_ms: 1000 },
          { id: 'l2-b', start_ms: 1000, duration_ms: 1000 }, // adjacent, no overlap
        ],
      },
    ]
    const result = detectOverlapsInGroups(groups)
    expect(result.has('l1-a')).toBe(true)
    expect(result.has('l1-b')).toBe(true)
    expect(result.has('l2-a')).toBe(false)
    expect(result.has('l2-b')).toBe(false)
  })

  it('clips in different groups never overlap each other', () => {
    // Same time range, but on different layers → no overlap
    const groups = [
      { clips: [{ id: 'a', start_ms: 0, duration_ms: 5000 }] },
      { clips: [{ id: 'b', start_ms: 0, duration_ms: 5000 }] },
    ]
    const result = detectOverlapsInGroups(groups)
    expect(result.size).toBe(0)
  })

  it('merges results from multiple overlapping groups', () => {
    const groups = [
      {
        clips: [
          { id: 'g1-a', start_ms: 0, duration_ms: 2000 },
          { id: 'g1-b', start_ms: 500, duration_ms: 2000 },
        ],
      },
      {
        clips: [
          { id: 'g2-a', start_ms: 100, duration_ms: 300 },
          { id: 'g2-b', start_ms: 200, duration_ms: 300 },
        ],
      },
    ]
    const result = detectOverlapsInGroups(groups)
    expect(result.size).toBe(4)
    expect(result.get('g1-a')!.has('g1-b')).toBe(true)
    expect(result.get('g2-a')!.has('g2-b')).toBe(true)
  })

  it('empty groups list returns empty map', () => {
    expect(detectOverlapsInGroups([]).size).toBe(0)
  })
})
