/**
 * Utility for detecting overlapping clips within the same layer/track.
 *
 * Uses sorted comparison for O(n log n) performance.
 * Related: Issue #177 — overlap detection alert.
 */

interface ClipInterval {
  id: string
  start_ms: number
  duration_ms: number
}

/**
 * Detect overlapping clips within a single layer or track.
 * Returns a Map from clipId to a Set of overlapping clipIds.
 *
 * Algorithm: sort by start_ms, then do a linear sweep comparing
 * each clip's end time against subsequent clips' start times.
 * This is O(n log n) overall (dominated by sort).
 */
export function detectOverlaps(clips: ClipInterval[]): Map<string, Set<string>> {
  const overlaps = new Map<string, Set<string>>()

  if (clips.length < 2) return overlaps

  // Sort by start time — O(n log n)
  const sorted = [...clips].sort((a, b) => a.start_ms - b.start_ms)

  for (let i = 0; i < sorted.length; i++) {
    const a = sorted[i]
    const aEnd = a.start_ms + a.duration_ms

    for (let j = i + 1; j < sorted.length; j++) {
      const b = sorted[j]

      // Because sorted by start, if b.start >= aEnd there is no more overlap possible
      if (b.start_ms >= aEnd) break

      const bEnd = b.start_ms + b.duration_ms
      if (a.start_ms < bEnd && aEnd > b.start_ms) {
        if (!overlaps.has(a.id)) overlaps.set(a.id, new Set())
        if (!overlaps.has(b.id)) overlaps.set(b.id, new Set())
        overlaps.get(a.id)!.add(b.id)
        overlaps.get(b.id)!.add(a.id)
      }
    }
  }

  return overlaps
}
