import { useState, useEffect, memo, useRef } from 'react'
import { assetsApi } from '@/api/assets'

interface VideoClipThumbnailsProps {
  projectId: string
  assetId: string
  clipWidth: number
  durationMs: number
  inPointMs: number
}

// Global cache for thumbnail URLs
const thumbnailCache = new Map<string, string>()

// Single thumbnail component
interface ThumbnailProps {
  projectId: string
  assetId: string
  timeMs: number
  width: number
  height: number
  delay?: number  // Delay before fetching (for progressive loading)
}

const Thumbnail = memo(function Thumbnail({
  projectId,
  assetId,
  timeMs,
  width,
  height,
  delay = 0,
}: ThumbnailProps) {
  const [url, setUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true

    const cacheKey = `${projectId}:${assetId}:${timeMs}:${width}:${height}`

    // Check cache first
    if (thumbnailCache.has(cacheKey)) {
      setUrl(thumbnailCache.get(cacheKey)!)
      setIsLoading(false)
      return
    }

    const fetchThumbnail = async () => {
      // Wait for delay before fetching (progressive loading)
      if (delay > 0) {
        await new Promise(resolve => setTimeout(resolve, delay))
      }

      try {
        const response = await assetsApi.getThumbnail(
          projectId,
          assetId,
          timeMs,
          width,
          height
        )
        thumbnailCache.set(cacheKey, response.url)
        setUrl(response.url)
        setIsLoading(false)
      } catch {
        setHasError(true)
        setIsLoading(false)
      }
    }

    fetchThumbnail()
  }, [projectId, assetId, timeMs, width, height, delay])

  // Show loading placeholder
  if (isLoading) {
    return (
      <div
        className="animate-pulse rounded-sm bg-gray-700/50"
        style={{
          width,
          height,
        }}
      />
    )
  }

  // Show error state with film icon
  if (hasError || !url) {
    return (
      <div
        className="bg-gray-600/40 flex items-center justify-center rounded-sm"
        style={{
          width,
          height,
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

  return (
    <img
      src={url}
      alt=""
      className="object-cover opacity-70 rounded-sm"
      style={{
        width,
        height,
      }}
      loading="lazy"
    />
  )
})

/**
 * Displays sparse thumbnails from a video clip.
 * Thumbnails are loaded progressively at 60s then 30s intervals.
 */
const VideoClipThumbnails = memo(function VideoClipThumbnails({
  projectId,
  assetId,
  clipWidth,
  durationMs,
  inPointMs,
}: VideoClipThumbnailsProps) {
  // Clip container is ~40px tall (h-12 layer with top-1/bottom-1)
  // Leave 2px padding top/bottom for visual balance
  const thumbHeight = 36
  const thumbWidth = Math.round(thumbHeight * (16 / 9))
  const thumbTop = 2  // Center vertically with 2px padding

  // Calculate sparse time positions
  // Phase 1: 60-second intervals (immediate)
  // Phase 2: 30-second intervals (delayed)
  const timePositions: { timeMs: number; delay: number; position: number }[] = []

  // Phase 1: 60-second intervals (or start/end for short clips)
  const interval1 = 60 * 1000 // 60 seconds
  for (let t = 0; t <= durationMs; t += interval1) {
    const position = (t / durationMs) * clipWidth
    timePositions.push({
      timeMs: inPointMs + t,
      delay: 0,
      position,
    })
  }

  // Phase 2: 30-second intervals (not already covered by 60s)
  const interval2 = 30 * 1000 // 30 seconds
  for (let t = interval2; t < durationMs; t += interval2) {
    // Skip if already covered by 60s interval
    if (t % interval1 === 0) continue

    const position = (t / durationMs) * clipWidth
    timePositions.push({
      timeMs: inPointMs + t,
      delay: 500, // Load after 500ms
      position,
    })
  }

  // For very short clips (< 30s), just show start
  if (timePositions.length === 0) {
    timePositions.push({
      timeMs: inPointMs,
      delay: 0,
      position: 0,
    })
  }

  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{ overflow: 'visible' }}
    >
      {timePositions.map(({ timeMs, delay, position }) => (
        <div
          key={`${assetId}-${timeMs}`}
          className="absolute"
          style={{
            left: position,
            top: thumbTop,
            height: thumbHeight,
            width: thumbWidth,
          }}
        >
          <Thumbnail
            projectId={projectId}
            assetId={assetId}
            timeMs={timeMs}
            width={thumbWidth}
            height={thumbHeight}
            delay={delay}
          />
        </div>
      ))}
    </div>
  )
})

export default VideoClipThumbnails

/**
 * Clears the thumbnail cache (useful for memory management).
 */
export function clearThumbnailCache(): void {
  thumbnailCache.clear()
}
