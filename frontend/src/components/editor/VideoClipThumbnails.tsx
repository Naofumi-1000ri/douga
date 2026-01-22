import { useState, useEffect, memo, useRef, useMemo } from 'react'
import { assetsApi } from '@/api/assets'

interface VideoClipThumbnailsProps {
  projectId: string
  assetId: string
  clipWidth: number
  durationMs: number
  inPointMs: number
  clipHeight?: number  // Optional: height of the clip container (defaults to 40)
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
  const lastFetchKey = useRef<string | null>(null)

  useEffect(() => {
    const cacheKey = `${projectId}:${assetId}:${timeMs}:${width}:${height}`

    // Skip if already fetched with same params
    if (lastFetchKey.current === cacheKey) return
    lastFetchKey.current = cacheKey

    // Check cache first
    if (thumbnailCache.has(cacheKey)) {
      setUrl(thumbnailCache.get(cacheKey)!)
      setIsLoading(false)
      return
    }

    setIsLoading(true)
    setHasError(false)

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
      className="object-cover rounded-sm"
      style={{
        width,
        height,
      }}
      loading="lazy"
    />
  )
})

/**
 * Displays tiled thumbnails from a video clip, filling the entire clip width.
 * Thumbnails are positioned side-by-side like a filmstrip.
 * Limited to max 20 thumbnails for performance.
 */
const VideoClipThumbnails = memo(function VideoClipThumbnails({
  projectId,
  assetId,
  clipWidth,
  durationMs,
  inPointMs,
  clipHeight = 40,  // Default to 40px (original h-12 layer)
}: VideoClipThumbnailsProps) {
  // Calculate thumbnail dimensions based on clip height
  // Leave 4px padding (2px top + 2px bottom) for visual balance
  const thumbHeight = Math.max(24, clipHeight - 4)  // Minimum 24px height
  const thumbWidth = Math.round(thumbHeight * (16 / 9))
  const thumbTop = 2  // Center vertically with 2px padding

  // Memoize thumbnail calculations
  const thumbnails = useMemo(() => {
    // Calculate how many thumbnails fit, limit to 20 for performance
    const maxThumbs = Math.min(20, Math.max(1, Math.floor(clipWidth / thumbWidth)))
    const result: { timeMs: number; delay: number; position: number }[] = []

    for (let i = 0; i < maxThumbs; i++) {
      const position = i * thumbWidth
      // Calculate time: spread thumbnails across the duration
      // For N thumbnails, we want times at 0%, 1/(N-1), 2/(N-1), ..., 100% of duration
      const progress = maxThumbs > 1 ? i / (maxThumbs - 1) : 0
      const timeMs = inPointMs + Math.round(progress * durationMs)

      // Progressive loading: immediate for first 3, then staggered
      const delay = i < 3 ? 0 : Math.min((i - 2) * 100, 500)

      result.push({ timeMs, delay, position })
    }

    return result
  }, [clipWidth, thumbWidth, inPointMs, durationMs])

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden">
      {thumbnails.map(({ timeMs, delay, position }, index) => (
        <div
          key={`${assetId}-${index}`}
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
