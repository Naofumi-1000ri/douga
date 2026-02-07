import { useState, useEffect, memo, useMemo, useRef } from 'react'
import { assetsApi, GridThumbnailsResponse } from '@/api/assets'

interface VideoClipThumbnailsProps {
  projectId: string
  assetId: string
  clipWidth: number
  inPointMs: number
  durationMs: number   // Timeline duration (ms)
  speed: number        // Playback speed
  clipHeight?: number  // Optional: height of the clip container (defaults to 40)
}

// Global cache for grid thumbnails (per asset)
const gridThumbnailCache = new Map<string, GridThumbnailsResponse>()

// Grid interval for pre-generated thumbnails (1 second)
const GRID_INTERVAL_MS = 1000

// Polling: 1s interval, 5min timeout
const POLL_INTERVAL_MS = 1000
const POLL_TIMEOUT_MS = 300000

function snapToGrid(timeMs: number): number {
  return Math.round(timeMs / GRID_INTERVAL_MS) * GRID_INTERVAL_MS
}

function getGridCacheKey(projectId: string, assetId: string): string {
  return `${projectId}:${assetId}`
}

/**
 * Displays tiled thumbnails from a video clip, filling the entire clip width.
 *
 * Architecture:
 *   [全部作成] BASE: Upload triggers BackgroundTask to generate ALL 1s-interval thumbnails.
 *   [UX改善] OPTION: Frontend polls for visible thumbnails, shows them as they appear.
 *                     Hints backend to prioritize visible times via generate-priority-thumbnails.
 *
 * Polling uses refs to avoid stale closures — setInterval always reads latest state.
 */
const VideoClipThumbnails = memo(function VideoClipThumbnails({
  projectId,
  assetId,
  clipWidth,
  inPointMs,
  durationMs,
  speed,
  clipHeight = 40,
}: VideoClipThumbnailsProps) {
  // State for rendering
  const [gridThumbnails, setGridThumbnails] = useState<Record<number, string> | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [, setHasError] = useState(false)
  const [visibleCount, setVisibleCount] = useState(0)

  // Refs for polling (avoid stale closures in setInterval)
  const gridThumbnailsRef = useRef<Record<number, string>>({})
  const neededTimesRef = useRef<number[]>([])
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollStartRef = useRef<number>(0)
  const isMountedRef = useRef(true)
  const fetchedAssetRef = useRef<string | null>(null)
  const priorityRequestedRef = useRef<string | null>(null)

  // Keep gridThumbnailsRef in sync with state
  useEffect(() => {
    gridThumbnailsRef.current = gridThumbnails ?? {}
  }, [gridThumbnails])

  // Calculate thumbnail dimensions
  const thumbHeight = Math.max(24, clipHeight - 4)
  const thumbWidth = Math.round(thumbHeight * (16 / 9))

  // Calculate all thumbnail positions and their snapped times
  const thumbnailData = useMemo(() => {
    const thumbCount = Math.max(1, Math.floor(clipWidth / thumbWidth))
    const result: { snappedTimeMs: number; position: number }[] = []
    for (let i = 0; i < thumbCount; i++) {
      const position = i * thumbWidth
      const timelineOffsetMs = (position / clipWidth) * durationMs
      const rawTimeMs = clipWidth > 0
        ? Math.round(inPointMs + timelineOffsetMs * speed)
        : inPointMs
      result.push({ snappedTimeMs: snapToGrid(rawTimeMs), position })
    }
    return result
  }, [clipWidth, thumbWidth, inPointMs, durationMs, speed])

  // Unique times needed by the current view
  const neededTimes = useMemo(
    () => [...new Set(thumbnailData.map(t => t.snappedTimeMs))],
    [thumbnailData],
  )

  // Keep neededTimesRef in sync
  useEffect(() => {
    neededTimesRef.current = neededTimes
  }, [neededTimes])

  // Cleanup on unmount
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [])

  // ── Polling logic (all via refs, no stale closures) ──

  const stopPolling = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current)
      pollIntervalRef.current = null
    }
  }

  const startPolling = (pId: string, aId: string) => {
    stopPolling()
    pollStartRef.current = Date.now()

    console.log('[VideoClipThumbnails] Starting poll:', { assetId: aId })

    // [UX改善] Hint backend to prioritize visible thumbnails
    const hintKey = `${pId}:${aId}`
    if (priorityRequestedRef.current !== hintKey) {
      priorityRequestedRef.current = hintKey
      assetsApi.generatePriorityThumbnails(pId, aId, neededTimesRef.current).catch(() => {})
    }

    pollIntervalRef.current = setInterval(async () => {
      if (!isMountedRef.current) { stopPolling(); return }
      if (Date.now() - pollStartRef.current > POLL_TIMEOUT_MS) {
        console.log('[VideoClipThumbnails] Poll timeout:', { assetId: aId })
        stopPolling()
        return
      }

      // Read latest state from refs
      const current = gridThumbnailsRef.current
      const needed = neededTimesRef.current
      const missing = needed.filter(t => !current[t])

      if (missing.length === 0) {
        console.log('[VideoClipThumbnails] All visible thumbnails found, stop poll:', { assetId: aId })
        stopPolling()
        return
      }

      try {
        const response = await assetsApi.getGridThumbnails(pId, aId, missing)
        if (!isMountedRef.current) return

        const newThumbs = response.thumbnails
        if (Object.keys(newThumbs).length > 0) {
          setGridThumbnails(prev => ({ ...prev, ...newThumbs }))
          setIsLoading(false)
          setHasError(false)
        }
      } catch {
        // Ignore — will retry on next interval
      }
    }, POLL_INTERVAL_MS)
  }

  // ── Initial fetch (runs once per asset) ──

  useEffect(() => {
    const cacheKey = getGridCacheKey(projectId, assetId)

    if (fetchedAssetRef.current === cacheKey) return
    fetchedAssetRef.current = cacheKey

    // Check global cache
    const cached = gridThumbnailCache.get(cacheKey)
    if (cached) {
      const thumbs = cached.thumbnails
      setGridThumbnails(thumbs)
      setIsLoading(false)
      setHasError(false)
      // Even cached data might be incomplete — check
      const needed = neededTimesRef.current
      if (needed.some(t => !thumbs[t])) {
        startPolling(projectId, assetId)
      }
      return
    }

    // Fetch from API
    const doFetch = async () => {
      setIsLoading(true)
      setHasError(false)

      try {
        // [UX改善] Fetch priority thumbnails first (visible area, fast)
        const priorityTimes = [...new Set(thumbnailData.slice(0, 10).map(t => t.snappedTimeMs))]
        const priorityResponse = await assetsApi.getGridThumbnails(projectId, assetId, priorityTimes)
        if (!isMountedRef.current) return
        setGridThumbnails(priorityResponse.thumbnails)
        setIsLoading(false)

        // [全部作成] Fetch full set to see what's available
        const fullResponse = await assetsApi.getGridThumbnails(projectId, assetId)
        if (!isMountedRef.current) return
        setGridThumbnails(fullResponse.thumbnails)

        const fullThumbs = fullResponse.thumbnails
        const count = Object.keys(fullThumbs).length
        console.log('[VideoClipThumbnails] Loaded:', { assetId, count })

        // If all visible thumbnails present, cache and done
        const needed = neededTimesRef.current
        if (!needed.some(t => !fullThumbs[t])) {
          gridThumbnailCache.set(cacheKey, fullResponse)
        } else {
          // Some still missing — backend is still generating, start polling
          console.log('[VideoClipThumbnails] Missing thumbnails, polling:', {
            assetId, available: count, needed: needed.length,
          })
          startPolling(projectId, assetId)
        }
      } catch (error) {
        console.error('[VideoClipThumbnails] Fetch failed:', error)
        if (!isMountedRef.current) return
        setHasError(true)
        setIsLoading(false)
        // Start polling — thumbnails may not exist yet
        startPolling(projectId, assetId)
      }
    }

    doFetch()

    return () => stopPolling()
    // Only depends on asset identity — NOT on startPolling/stopPolling/thumbnailData
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, assetId])

  // ── Sequential reveal animation ──

  useEffect(() => {
    if (!gridThumbnails || isLoading) {
      if (!gridThumbnails) setVisibleCount(0)
      return
    }
    const totalThumbs = thumbnailData.length
    if (visibleCount >= totalThumbs) return

    const timer = setTimeout(() => {
      setVisibleCount(prev => Math.min(prev + 3, totalThumbs))
    }, 16)
    return () => clearTimeout(timer)
  }, [gridThumbnails, isLoading, visibleCount, thumbnailData.length])

  // ── Render ──

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden flex items-center">
      {thumbnailData.map(({ snappedTimeMs }, index) => {
        const url = gridThumbnails?.[snappedTimeMs]
        const isRevealed = index < visibleCount

        // Loading placeholder
        if ((isLoading && !url) || (!isRevealed && url)) {
          return (
            <div
              key={`${assetId}-${index}`}
              className="flex-shrink-0 animate-pulse rounded-sm bg-gray-700/50"
              style={{ width: thumbWidth, height: thumbHeight }}
            />
          )
        }

        // Missing thumbnail (generating or error)
        if (!url) {
          const isPolling = pollIntervalRef.current !== null
          return (
            <div
              key={`${assetId}-${index}`}
              className={`flex-shrink-0 flex items-center justify-center rounded-sm ${
                isPolling ? 'animate-pulse bg-gray-700/50' : 'bg-gray-600/40'
              }`}
              style={{ width: thumbWidth, height: thumbHeight }}
            >
              <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" />
              </svg>
            </div>
          )
        }

        // Thumbnail image
        return (
          <div
            key={`${assetId}-${index}`}
            className="flex-shrink-0"
            style={{ height: thumbHeight, width: thumbWidth }}
          >
            <img
              src={url}
              alt=""
              className="object-cover rounded-sm"
              style={{ width: thumbWidth, height: thumbHeight }}
              loading="lazy"
            />
          </div>
        )
      })}
    </div>
  )
})

export default VideoClipThumbnails

export function clearGridThumbnailCache(): void {
  gridThumbnailCache.clear()
}
