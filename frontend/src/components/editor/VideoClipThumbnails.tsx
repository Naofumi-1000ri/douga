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

/**
 * Snaps a time value to the nearest 1-second grid boundary.
 * Pre-generated thumbnails exist at 0, 1000, 2000, ... ms.
 */
function snapToGrid(timeMs: number): number {
  return Math.round(timeMs / GRID_INTERVAL_MS) * GRID_INTERVAL_MS
}

/**
 * Generates a cache key for grid thumbnails
 */
function getGridCacheKey(projectId: string, assetId: string): string {
  return `${projectId}:${assetId}`
}

/**
 * Displays tiled thumbnails from a video clip, filling the entire clip width.
 * Thumbnails are positioned side-by-side like a filmstrip.
 *
 * Uses pre-generated grid thumbnails (1-second intervals) for instant display.
 * Grid thumbnails are generated at upload time and stored in GCS.
 * This eliminates the 50-second delay when changing timeline zoom levels.
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
  // State for grid thumbnail URLs (map of time_ms -> URL)
  const [gridThumbnails, setGridThumbnails] = useState<Record<number, string> | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)

  // Sequential loading: track how many thumbnails are allowed to load
  const [visibleCount, setVisibleCount] = useState(0)

  // Track if we've fetched grid thumbnails for this asset
  const fetchedAssetRef = useRef<string | null>(null)

  // Calculate thumbnail dimensions based on clip height
  const thumbHeight = Math.max(24, clipHeight - 4)
  const thumbWidth = Math.round(thumbHeight * (16 / 9))

  // Calculate all thumbnail positions and their snapped times
  const thumbnailData = useMemo(() => {
    // No limit on thumbnail count - grid thumbnails are instant from GCS
    const thumbCount = Math.max(1, Math.floor(clipWidth / thumbWidth))
    const result: { snappedTimeMs: number; position: number }[] = []

    for (let i = 0; i < thumbCount; i++) {
      const position = i * thumbWidth
      // timelineOffset = how far into the clip (in timeline ms)
      // sourceTime = in_point + timelineOffset * speed
      const timelineOffsetMs = (position / clipWidth) * durationMs
      const rawTimeMs = clipWidth > 0
        ? Math.round(inPointMs + timelineOffsetMs * speed)
        : inPointMs

      // Snap to 1-second grid for instant lookup
      const snappedTimeMs = snapToGrid(rawTimeMs)
      result.push({ snappedTimeMs, position })
    }

    return result
  }, [clipWidth, thumbWidth, inPointMs, durationMs, speed])

  // Fetch grid thumbnails with priority loading (first 10 visible, then rest)
  useEffect(() => {
    const cacheKey = getGridCacheKey(projectId, assetId)

    // Skip if already fetched for this asset
    if (fetchedAssetRef.current === cacheKey) {
      return
    }
    fetchedAssetRef.current = cacheKey

    // Check global cache first
    const cached = gridThumbnailCache.get(cacheKey)
    if (cached) {
      console.log('[VideoClipThumbnails] Using cached grid thumbnails:', {
        assetId,
        thumbnailCount: Object.keys(cached.thumbnails).length,
      })
      setGridThumbnails(cached.thumbnails)
      setIsLoading(false)
      setHasError(false)
      return
    }

    // Priority loading: fetch first 10 visible thumbnails, then the rest
    const fetchWithPriority = async () => {
      setIsLoading(true)
      setHasError(false)

      try {
        // Get unique snapped times from current thumbnailData (first 10)
        const priorityTimes = [...new Set(thumbnailData.slice(0, 10).map(t => t.snappedTimeMs))]

        console.log('[VideoClipThumbnails] Fetching priority thumbnails:', {
          assetId,
          count: priorityTimes.length,
          times: priorityTimes,
        })

        // First: fetch only the priority thumbnails (fast! ~0.5s)
        const priorityResponse = await assetsApi.getGridThumbnails(projectId, assetId, priorityTimes)
        setGridThumbnails(priorityResponse.thumbnails)
        setIsLoading(false)

        // Then: fetch all thumbnails in background (slower, but user doesn't wait)
        const fullResponse = await assetsApi.getGridThumbnails(projectId, assetId)
        gridThumbnailCache.set(cacheKey, fullResponse)
        setGridThumbnails(fullResponse.thumbnails)

        console.log('[VideoClipThumbnails] Full thumbnails loaded:', {
          assetId,
          thumbnailCount: Object.keys(fullResponse.thumbnails).length,
        })
      } catch (error) {
        console.error('[VideoClipThumbnails] Failed to fetch grid thumbnails:', error)
        setHasError(true)
        setIsLoading(false)
      }
    }

    fetchWithPriority()
  }, [projectId, assetId, thumbnailData])

  // Sequential loading: gradually reveal thumbnails from left to right
  useEffect(() => {
    if (!gridThumbnails || isLoading) {
      setVisibleCount(0)
      return
    }

    const totalThumbs = thumbnailData.length
    if (visibleCount >= totalThumbs) return

    // Reveal 3 thumbnails at a time, every 16ms (60fps)
    const batchSize = 3
    const timer = setTimeout(() => {
      setVisibleCount((prev) => Math.min(prev + batchSize, totalThumbs))
    }, 16)

    return () => clearTimeout(timer)
  }, [gridThumbnails, isLoading, visibleCount, thumbnailData.length])

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden flex items-center">
      {thumbnailData.map(({ snappedTimeMs }, index) => {
        const url = gridThumbnails?.[snappedTimeMs]
        // Sequential loading: only show thumbnails up to visibleCount
        const isRevealed = index < visibleCount

        // Show loading placeholder (either API loading or waiting for sequential reveal)
        if ((isLoading && !url) || (!isRevealed && url)) {
          return (
            <div
              key={`${assetId}-${index}`}
              className="flex-shrink-0 animate-pulse rounded-sm bg-gray-700/50"
              style={{
                width: thumbWidth,
                height: thumbHeight,
              }}
            />
          )
        }

        // Show error state with film icon
        if ((hasError && !url) || (!isLoading && !url)) {
          return (
            <div
              key={`${assetId}-${index}`}
              className="flex-shrink-0 bg-gray-600/40 flex items-center justify-center rounded-sm"
              style={{
                width: thumbWidth,
                height: thumbHeight,
              }}
            >
              <svg
                className="w-4 h-4 text-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z"
                />
              </svg>
            </div>
          )
        }

        // Show thumbnail image
        return (
          <div
            key={`${assetId}-${index}`}
            className="flex-shrink-0"
            style={{
              height: thumbHeight,
              width: thumbWidth,
            }}
          >
            <img
              src={url}
              alt=""
              className="object-cover rounded-sm"
              style={{
                width: thumbWidth,
                height: thumbHeight,
              }}
              loading="lazy"
            />
          </div>
        )
      })}
    </div>
  )
})

export default VideoClipThumbnails

/**
 * Clears the grid thumbnail cache (useful for memory management).
 */
export function clearGridThumbnailCache(): void {
  gridThumbnailCache.clear()
}
